# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

# pyright: reportPrivateUsage=false
"""Tests for the state locking/atomic-write machinery, the sub-agent hierarchy
stack helpers, and the deterministic transcript-by-agent_id prompt recovery."""

import asyncio
import json
import multiprocessing as mp
import os
import shutil
import tempfile
import time
from pathlib import Path
from typing import Any

import pytest
from adrian_cc import agent
from adrian_cc.agent import (
    _build_event,
    _pop_subagent,
    _push_subagent,
    _reset_state,
    _subagent_delegated_prompt,
)
from adrian_cc.proto import event_pb2 as pb


def _make_hook_data(**overrides: Any) -> dict[str, Any]:
    base = {
        "session_id": "s1",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hi"},
        "tool_use_id": "toolu_1",
        "cwd": "/proj",
        "transcript_path": "",
        "permission_mode": "default",
    }
    base.update(overrides)
    return base


# module-level worker so it survives fork in the concurrency test
def _hit_worker(i: int) -> None:
    agent._mutate_state(lambda s: s.setdefault("hits", []).append(i))


# ---------------------------------------------------------------
# Locking + atomic state I/O (state files redirected to a tmp dir)
# ---------------------------------------------------------------


class TestStateLocking:
    _orig: tuple[Any, ...] = ()
    tmp: Path = Path()

    def setup_method(self) -> None:
        self._orig = (agent._STATE_DIR, agent._STATE_FILE, agent._STATE_LOCK)
        self.tmp = Path(tempfile.mkdtemp())
        agent._STATE_DIR = self.tmp
        agent._STATE_FILE = self.tmp / "cc-state.json"
        agent._STATE_LOCK = self.tmp / "adrian-cc-state.lock"

    def teardown_method(self) -> None:
        agent._STATE_DIR, agent._STATE_FILE, agent._STATE_LOCK = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_save_load_roundtrip(self) -> None:
        agent._save_state({"agent_stack": [{"agent_id": "a"}], "invocation_count": 3})
        loaded = agent._load_state()
        assert loaded["agent_stack"][0]["agent_id"] == "a"
        assert loaded["invocation_count"] == 3

    def test_atomic_write_leaves_no_temp_files(self) -> None:
        agent._save_state({"agent_stack": []})
        leftovers = list(self.tmp.glob(".cc-state-*.tmp"))
        assert leftovers == [], f"temp files not cleaned up: {leftovers}"

    def test_mutate_state_persists_and_returns_value(self) -> None:
        _reset_state()
        ret = agent._mutate_state(
            lambda s: (
                s["agent_stack"].append({"agent_id": "x", "spawn_id": "t"}) or "done"
            )
        )
        assert ret == "done"
        assert agent._load_state()["agent_stack"][-1]["agent_id"] == "x"

    def test_reset_state(self) -> None:
        agent._save_state(
            {"agent_stack": [{"agent_id": "junk"}], "invocation_count": 9}
        )
        _reset_state()
        state = agent._load_state()
        assert [f["agent_id"] for f in state["agent_stack"]] == ["claude-code"]
        assert state["invocation_count"] == 0

    def test_state_lock_creates_lockfile_and_is_a_contextmanager(self) -> None:
        with agent._state_lock():
            pass
        if agent.fcntl is not None:
            assert agent._STATE_LOCK.exists()

    def test_concurrent_mutations_no_lost_updates(self) -> None:
        if agent.fcntl is None or not hasattr(os, "fork"):
            pytest.skip("POSIX flock + fork required for the contention test")
        _reset_state()
        ctx = mp.get_context("fork")  # fork so children inherit the patched paths
        n = 30
        procs = [ctx.Process(target=_hit_worker, args=(i,)) for i in range(n)]
        for p in procs:
            p.start()
        for p in procs:
            p.join()
        hits = agent._load_state().get("hits", [])
        assert len(hits) == n, f"lost updates: got {len(hits)} of {n}"
        assert set(hits) == set(range(n))


# ---------------------------------------------------------------
# Sub-agent hierarchy stack (pure dict operations)
# ---------------------------------------------------------------


class TestSubagentStack:
    def _root(self) -> dict[str, Any]:
        return {"agent_stack": [{"agent_id": "claude-code", "spawn_id": ""}]}

    def test_push_frame_fields(self) -> None:
        s = self._root()
        _push_subagent(
            s,
            "toolu_9",
            {
                "subagent_type": "general-purpose",
                "description": "Find the bug",
                "prompt": "do work",
            },
        )
        frame = s["agent_stack"][-1]
        assert frame["agent_id"] == "general-purpose:find-the-bug"
        assert frame["spawn_id"] == "toolu_9"
        assert frame["subagent_type"] == "general-purpose"
        # prompt is NOT stored in state anymore (recovered from transcript instead)
        assert "prompt" not in frame
        assert "native_agent_id" not in frame

    def test_push_no_description(self) -> None:
        s = self._root()
        _push_subagent(s, "t", {"subagent_type": "Explore"})
        assert s["agent_stack"][-1]["agent_id"] == "Explore"

    def test_pop_matching_top(self) -> None:
        s = self._root()
        _push_subagent(s, "t1", {"subagent_type": "Explore", "description": "x"})
        _pop_subagent(s, "t1")
        assert len(s["agent_stack"]) == 1

    def test_pop_never_pops_root(self) -> None:
        s = self._root()
        _pop_subagent(s, "")  # spawn_id of root is ""
        assert len(s["agent_stack"]) == 1

    def test_pop_mismatch_is_noop(self) -> None:
        s = self._root()
        _push_subagent(s, "t1", {"subagent_type": "Explore"})
        _pop_subagent(s, "different-id")
        assert len(s["agent_stack"]) == 2


# ---------------------------------------------------------------
# Deterministic transcript-by-agent_id prompt recovery
# ---------------------------------------------------------------


class TestSubagentDelegatedPrompt:
    tmp: Path = Path()

    def setup_method(self) -> None:
        self.tmp = Path(tempfile.mkdtemp())

    def teardown_method(self) -> None:
        shutil.rmtree(self.tmp, ignore_errors=True)

    def _write(
        self,
        agent_id: str,
        entries: list[dict[str, Any]],
        session: str = "sess",
    ) -> str:
        """Lay out <tmp>/<session>.jsonl (main) + the sub-agent transcript at
        <tmp>/<session>/subagents/agent-<agent_id>.jsonl. Returns the MAIN path."""
        main_tp = self.tmp / f"{session}.jsonl"
        sub = self.tmp / session / "subagents" / f"agent-{agent_id}.jsonl"
        sub.parent.mkdir(parents=True, exist_ok=True)
        with open(sub, "w", encoding="utf-8") as f:
            for e in entries:
                f.write(json.dumps(e) + "\n")
        return str(main_tp)

    def test_recovers_first_user_string(self) -> None:
        main = self._write(
            "abc123",
            [
                {
                    "type": "user",
                    "message": {"role": "user", "content": "DELEGATED TASK: do X"},
                },
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "ok"}]},
                },
            ],
        )
        assert _subagent_delegated_prompt(main, "abc123") == "DELEGATED TASK: do X"

    def test_recovers_first_user_list_blocks(self) -> None:
        main = self._write(
            "abc123",
            [
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "text", "text": "part one"},
                            {"type": "text", "text": "part two"},
                        ]
                    },
                },
            ],
        )
        assert _subagent_delegated_prompt(main, "abc123") == "part one part two"

    def test_returns_first_user_only(self) -> None:
        main = self._write(
            "abc123",
            [
                {"type": "user", "message": {"content": "FIRST"}},
                {"type": "user", "message": {"content": "SECOND"}},
            ],
        )
        assert _subagent_delegated_prompt(main, "abc123") == "FIRST"

    def test_missing_file_returns_empty(self) -> None:
        main = str(self.tmp / "sess.jsonl")
        assert _subagent_delegated_prompt(main, "no-such-agent") == ""

    def test_empty_agent_id_returns_empty(self) -> None:
        assert _subagent_delegated_prompt(str(self.tmp / "sess.jsonl"), "") == ""

    def test_empty_transcript_path_returns_empty(self) -> None:
        assert _subagent_delegated_prompt("", "abc123") == ""

    def test_skips_malformed_lines(self) -> None:
        # write a broken line then a valid user entry
        main_tp = self.tmp / "sess.jsonl"
        sub = self.tmp / "sess" / "subagents" / "agent-abc.jsonl"
        sub.parent.mkdir(parents=True, exist_ok=True)
        sub.write_text(
            "{not json}\n"
            + json.dumps({"type": "user", "message": {"content": "VALID"}})
            + "\n"
        )
        assert _subagent_delegated_prompt(str(main_tp), "abc") == "VALID"

    def test_no_user_message_returns_empty(self) -> None:
        main = self._write(
            "abc123",
            [
                {
                    "type": "assistant",
                    "message": {"content": [{"type": "text", "text": "hi"}]},
                },
            ],
        )
        assert _subagent_delegated_prompt(main, "abc123") == ""


# ---------------------------------------------------------------
# delegated_prompt wiring into the event
# ---------------------------------------------------------------


class TestDelegatedPromptInEvent:
    def test_delegated_prompt_becomes_system_prompt(self) -> None:
        hook = _make_hook_data(agent_id="nativeX", agent_type="general-purpose")
        event = _build_event(
            hook,
            {"agent_stack": [{"agent_id": "claude-code"}]},
            delegated_prompt="THE DELEGATION",
        )
        assert event.agent.system_prompt == "THE DELEGATION"

    def test_no_delegated_prompt_falls_back_empty(self) -> None:
        # no transcript, no delegated prompt -> empty system prompt (current CC)
        hook = _make_hook_data()
        event = _build_event(hook, {"agent_stack": [{"agent_id": "claude-code"}]})
        assert event.agent.system_prompt == ""


# ---------------------------------------------------------------
# Portable lock (fcntl POSIX / msvcrt Windows / no-op fallback)
# ---------------------------------------------------------------


class TestPortableLock:
    _orig: tuple[Any, ...] = ()
    tmp: Path = Path()

    def setup_method(self) -> None:
        self._orig = (
            agent._STATE_DIR,
            agent._STATE_FILE,
            agent._STATE_LOCK,
            agent.fcntl,
            agent.msvcrt,
        )
        self.tmp = Path(tempfile.mkdtemp())
        agent._STATE_DIR = self.tmp
        agent._STATE_FILE = self.tmp / "cc-state.json"
        agent._STATE_LOCK = self.tmp / "adrian-cc-state.lock"

    def teardown_method(self) -> None:
        (
            agent._STATE_DIR,
            agent._STATE_FILE,
            agent._STATE_LOCK,
            agent.fcntl,
            agent.msvcrt,
        ) = self._orig
        shutil.rmtree(self.tmp, ignore_errors=True)

    def test_no_locking_primitive_still_mutates(self) -> None:
        # Simulate a platform with neither fcntl nor msvcrt: lock is a no-op
        # but state mutation must still work single-process.
        agent.fcntl = None
        agent.msvcrt = None
        agent._reset_state()
        agent._mutate_state(lambda s: s.__setitem__("marker", 7))
        assert agent._load_state()["marker"] == 7

    def test_lock_is_exclusive_between_fds(self) -> None:
        # POSIX-only assertion of real mutual exclusion at the primitive level.
        if agent.fcntl is None:
            pytest.skip("POSIX fcntl required")
        agent._STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd1 = os.open(str(agent._STATE_LOCK), os.O_CREAT | os.O_RDWR, 0o600)
        fd2 = os.open(str(agent._STATE_LOCK), os.O_CREAT | os.O_RDWR, 0o600)
        try:
            assert agent._try_lock(fd1) is True  # first acquires
            assert agent._try_lock(fd2) is False  # second is blocked
            agent._unlock(fd1)
            assert agent._try_lock(fd2) is True  # freed -> second acquires
            agent._unlock(fd2)
        finally:
            os.close(fd1)
            os.close(fd2)

    def test_lock_degrades_when_lockfile_uncreatable(self) -> None:
        # If the lock file can't be created, _state_lock degrades to no-op
        # (yields) instead of raising - the mutation still runs.
        agent._STATE_LOCK = self.tmp / "nonexistent-dir" / "x.lock"
        # dir doesn't exist and mkdir of _STATE_DIR won't create the nested one
        with agent._state_lock():
            pass  # must not raise

    def test_atomic_replace_roundtrip(self) -> None:
        agent._save_state({"agent_stack": [], "v": 1})
        assert agent._load_state()["v"] == 1
        agent._save_state({"agent_stack": [], "v": 2})
        assert agent._load_state()["v"] == 2


# ---------------------------------------------------------------
# Hook-timeout ceiling: _send_event_sync must bound the round-trip
# ---------------------------------------------------------------


class TestSendEventTimeout:
    def _ev(self) -> pb.PairedEvent:
        return pb.PairedEvent(event_id="e", session_id="s")

    def test_bounds_a_hang_and_returns_promptly(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _hang(*_a: object, **_k: object) -> None:
            await asyncio.sleep(60)  # would hang forever without the ceiling

        monkeypatch.setattr(agent, "_ws_send_event", _hang)
        monkeypatch.setattr(agent, "MAX_HOOK_TIMEOUT", 0.3)
        t0 = time.monotonic()
        res = agent._send_event_sync(self._ev(), "s")
        dt = time.monotonic() - t0
        assert res["error"] == "max_hook_timeout"
        assert dt < 5, f"did not return promptly: {dt:.1f}s"

    def test_passes_through_normal_result(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _ok(*_a: object, **_k: object) -> dict[str, Any]:
            return {"mode": 1, "policy": None, "verdict": None, "error": None}

        monkeypatch.setattr(agent, "_ws_send_event", _ok)
        res = agent._send_event_sync(self._ev(), "s")
        assert res["error"] is None and res["mode"] == 1

    def test_transport_error_is_caught_not_raised(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        async def _boom(*_a: object, **_k: object) -> None:
            raise RuntimeError("kaboom")

        monkeypatch.setattr(agent, "_ws_send_event", _boom)
        res = agent._send_event_sync(self._ev(), "s")
        assert res["verdict"] is None and "kaboom" in res["error"]

    def test_default_ceiling_exceeds_verdict_timeouts(self) -> None:
        # The outer net must never cut a legitimate verdict wait.
        assert agent.MAX_HOOK_TIMEOUT > agent.VERDICT_TIMEOUT
        assert agent.MAX_HOOK_TIMEOUT > agent.HITL_TIMEOUT


# ---------------------------------------------------------------
# verify subcommand (used by /adrian-init) - never leaks the key
# ---------------------------------------------------------------


class TestVerifySubcommand:
    def test_no_key_fails_and_does_not_leak(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        monkeypatch.setattr(agent, "ADRIAN_API_KEY", "")
        with pytest.raises(SystemExit) as e:
            agent._handle_verify()
        assert e.value.code == 1
        out = capsys.readouterr().out
        assert "not set" in out
        assert "adr_live" not in out and "adr_local" not in out

    def test_verify_reports_ok_from_login_ack(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def _ok(*_a: object, **_k: object) -> dict[str, Any]:
            return {"ok": True, "source_ack": "claude-code", "mode_name": "ALERT"}

        monkeypatch.setattr(agent, "ADRIAN_API_KEY", "adr_live_x")
        monkeypatch.setattr(agent, "_verify_connection", _ok)
        with pytest.raises(SystemExit) as e:
            agent._handle_verify()
        assert e.value.code == 0
        out = capsys.readouterr().out
        assert out.startswith("OK:") and "claude-code" in out
        assert "adr_live_x" not in out  # key value never printed

    def test_verify_reports_fail(
        self, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
    ) -> None:
        async def _fail(*_a: object, **_k: object) -> dict[str, Any]:
            return {"ok": False, "error": "InvalidStatus: HTTP 401"}

        monkeypatch.setattr(agent, "ADRIAN_API_KEY", "adr_live_x")
        monkeypatch.setattr(agent, "_verify_connection", _fail)
        with pytest.raises(SystemExit) as e:
            agent._handle_verify()
        assert e.value.code == 1
        assert "FAIL" in capsys.readouterr().out
