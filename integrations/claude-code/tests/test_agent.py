# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

# pyright: reportPrivateUsage=false
"""Tests for the hook agent - event building, verdict handling, mode logic."""

import contextlib
import json
import os
import tempfile
from typing import Any

from adrian_cc.agent import (
    _STATE_FILE,
    MODE_DEFER_HITL,
    _build_event,
    _current_agent,
    _load_state,
    _parent_agent,
    _save_state,
    _verdict_action,
)
from adrian_cc.proto import event_pb2 as pb

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------


def _make_hook_data(**overrides: Any) -> dict[str, Any]:
    """Build a realistic Claude Code hook payload."""
    base: dict[str, Any] = {
        "session_id": "test-session-001",
        "hook_event_name": "PreToolUse",
        "tool_name": "Bash",
        "tool_input": {"command": "echo hello", "timeout": 120000},
        "tool_use_id": "toolu_01AbCd",
        "cwd": "/Users/dev/project",
        "transcript_path": "",
        "permission_mode": "default",
    }
    base.update(overrides)
    return base


def _make_transcript(lines: list[dict[str, Any]]) -> str:
    # delete=False: the file must outlive this helper (its path is returned).
    f = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for entry in lines:
        f.write(json.dumps(entry) + "\n")
    f.close()
    return f.name


def _default_state() -> dict[str, Any]:
    return {"agent_stack": [{"agent_id": "claude-code", "spawn_id": ""}]}


# ---------------------------------------------------------------
# State management
# ---------------------------------------------------------------


class TestState:
    def setup_method(self) -> None:
        with contextlib.suppress(Exception):
            _STATE_FILE.unlink(missing_ok=True)

    def teardown_method(self) -> None:
        with contextlib.suppress(Exception):
            _STATE_FILE.unlink(missing_ok=True)

    def test_load_default(self) -> None:
        state = _load_state()
        assert state["agent_stack"][0]["agent_id"] == "claude-code"

    def test_save_and_load(self) -> None:
        state = {
            "agent_stack": [
                {"agent_id": "claude-code", "spawn_id": ""},
                {"agent_id": "Explore:audit", "spawn_id": "toolu_99"},
            ]
        }
        _save_state(state)
        loaded = _load_state()
        assert len(loaded["agent_stack"]) == 2
        assert loaded["agent_stack"][1]["agent_id"] == "Explore:audit"

    def test_current_agent_root(self) -> None:
        assert _current_agent(_default_state()) == "claude-code"

    def test_current_agent_subagent(self) -> None:
        state = {
            "agent_stack": [
                {"agent_id": "claude-code", "spawn_id": ""},
                {"agent_id": "Explore:find-secrets", "spawn_id": "t1"},
            ]
        }
        assert _current_agent(state) == "Explore:find-secrets"

    def test_parent_agent_root(self) -> None:
        assert _parent_agent(_default_state()) == ""

    def test_parent_agent_subagent(self) -> None:
        state = {
            "agent_stack": [
                {"agent_id": "claude-code", "spawn_id": ""},
                {"agent_id": "Explore:audit", "spawn_id": "t1"},
            ]
        }
        assert _parent_agent(state) == "claude-code"

    def test_parent_agent_nested(self) -> None:
        state = {
            "agent_stack": [
                {"agent_id": "claude-code", "spawn_id": ""},
                {"agent_id": "Plan:design", "spawn_id": "t1"},
                {"agent_id": "Explore:research", "spawn_id": "t2"},
            ]
        }
        assert _current_agent(state) == "Explore:research"
        assert _parent_agent(state) == "Plan:design"


# ---------------------------------------------------------------
# Event building
# ---------------------------------------------------------------


class TestBuildEvent:
    def test_basic_fields(self) -> None:
        hook = _make_hook_data()
        event = _build_event(hook, _default_state())

        assert event.event_id  # non-empty UUID
        assert event.session_id == "test-session-001"
        assert event.pair_type == pb.PAIR_TYPE_TOOL
        assert event.tool.tool_name == "Bash"
        assert '"command": "echo hello"' in event.tool.input
        assert event.tool.tool_call_id == "toolu_01AbCd"
        assert event.run_id == "toolu_01AbCd"
        assert event.timestamp  # ISO 8601

    def test_agent_context(self) -> None:
        event = _build_event(_make_hook_data(), _default_state())
        assert event.agent.agent_id == "claude-code"
        assert event.parent.agent_id == ""

    def test_subagent_context(self) -> None:
        state = {
            "agent_stack": [
                {"agent_id": "claude-code", "spawn_id": ""},
                {"agent_id": "Explore:scan", "spawn_id": "t1"},
            ]
        }
        event = _build_event(_make_hook_data(), state)
        assert event.agent.agent_id == "Explore:scan"
        assert event.parent.agent_id == "claude-code"

    def test_tool_output(self) -> None:
        hook = _make_hook_data()
        event = _build_event(hook, _default_state(), output='{"stdout": "hello"}')
        assert event.tool.output == '{"stdout": "hello"}'

    def test_metadata_fields(self) -> None:
        hook = _make_hook_data(cwd="/project", permission_mode="bypassPermissions")
        event = _build_event(hook, _default_state())
        meta = json.loads(event.metadata_json.decode())
        assert meta["source"] == "claude-code"
        assert meta["cwd"] == "/project"
        assert meta["permission_mode"] == "bypassPermissions"
        assert "reasoning_block_count" in meta

    def test_transcript_enrichment(self) -> None:
        path = _make_transcript(
            [
                {"type": "system", "message": {"content": "Be secure."}},
                {"type": "user", "message": {"content": "Fix the auth bug"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "I need to check the JWT validation...",
                            },
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": "auth.py"},
                            },
                        ]
                    },
                },
            ]
        )
        try:
            hook = _make_hook_data(transcript_path=path)
            event = _build_event(hook, _default_state())

            assert event.agent.user_instruction == "Fix the auth bug"
            # system_prompt uses the real system message - NOT stale reasoning.
            assert event.agent.system_prompt == "Be secure."
            assert "JWT validation" not in event.agent.system_prompt

            # Reasoning is still surfaced, but only in metadata.
            meta = json.loads(event.metadata_json.decode())
            assert meta["reasoning_block_count"] >= 1
            assert "JWT validation" in meta["reasoning_latest"]
        finally:
            os.unlink(path)

    def test_system_prompt_no_reasoning_fallback(self) -> None:
        # No system message and no delegated prompt → system_prompt is empty,
        # never a prior turn's thinking block.
        path = _make_transcript(
            [
                {"type": "user", "message": {"content": "Do it"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "some private reasoning"},
                        ]
                    },
                },
            ]
        )
        try:
            hook = _make_hook_data(transcript_path=path)
            event = _build_event(hook, _default_state())
            assert event.agent.system_prompt == ""
        finally:
            os.unlink(path)

    def test_invocation_id_without_transcript(self) -> None:
        hook = _make_hook_data()
        event = _build_event(hook, _default_state())
        assert event.invocation_id == "test-session-001"

    def test_invocation_id_from_hook_prompt_id(self) -> None:
        # Claude Code passes prompt_id on every hook payload - used directly,
        # no transcript read, and wins over any transcript-derived id.
        path = _make_transcript(
            [
                {
                    "type": "user",
                    "promptId": "pid-transcript",
                    "message": {"content": "hi"},
                },
            ]
        )
        try:
            hook = _make_hook_data(prompt_id="pid-from-hook", transcript_path=path)
            event = _build_event(hook, _default_state())
            assert event.invocation_id == "pid-from-hook"
        finally:
            os.unlink(path)

    def test_invocation_id_uses_transcript_prompt_id_fallback(self) -> None:
        # No prompt_id on the payload → fall back to the transcript's promptId.
        path = _make_transcript(
            [
                {
                    "type": "user",
                    "promptId": "pid-999",
                    "message": {"content": "First prompt"},
                },
            ]
        )
        try:
            hook = _make_hook_data(transcript_path=path)
            event = _build_event(hook, _default_state())
            assert event.invocation_id == "pid-999"
        finally:
            os.unlink(path)

    def test_invocation_id_with_transcript_no_prompt_id(self) -> None:
        # Older transcripts without promptId fall back to a session+count hash.
        path = _make_transcript(
            [
                {"type": "user", "message": {"content": "First prompt"}},
            ]
        )
        try:
            hook = _make_hook_data(transcript_path=path)
            event = _build_event(hook, _default_state())
            assert event.invocation_id != "test-session-001"
            assert len(event.invocation_id) == 16
        finally:
            os.unlink(path)

    def test_all_tool_types(self) -> None:
        tools = [
            ("Bash", {"command": "ls", "timeout": 120000}),
            ("Read", {"file_path": "/etc/hosts"}),
            ("Write", {"file_path": "app.py", "content": "print('hi')"}),
            ("Edit", {"file_path": "a.py", "old_string": "x", "new_string": "y"}),
            ("Grep", {"pattern": "TODO", "path": "src/"}),
            ("Glob", {"pattern": "**/*.ts"}),
            ("WebFetch", {"url": "https://example.com"}),
            ("Agent", {"subagent_type": "Explore", "description": "Scan code"}),
            ("mcp__github__create_issue", {"repo": "org/repo", "title": "Bug"}),
        ]
        for tool_name, tool_input in tools:
            hook = _make_hook_data(tool_name=tool_name, tool_input=tool_input)
            event = _build_event(hook, _default_state())
            assert event.tool.tool_name == tool_name
            assert (
                tool_name.split("__")[-1] in event.tool.input
                or list(tool_input.keys())[0] in event.tool.input
            )

    def test_assistant_message_event(self) -> None:
        # Stop hook → an AssistantMessage event carrying Claude's reply, keyed
        # by prompt_id so it shares the turn's invocation_id with the prompt.
        stop_hook = {
            "session_id": "test-session-001",
            "tool_name": "AssistantMessage",
            "tool_input": {"message": "Hi! I can help with that."},
            "tool_use_id": "assistant-abc123",
            "hook_event_name": "Stop",
            "prompt_id": "pid-turn-7",
        }
        event = _build_event(stop_hook, _default_state())
        assert event.tool.tool_name == "AssistantMessage"
        assert "Hi! I can help with that." in event.tool.input
        assert event.invocation_id == "pid-turn-7"
        assert event.agent.agent_id == "claude-code"


# ---------------------------------------------------------------
# Verdict action mapping
# ---------------------------------------------------------------


class TestVerdictAction:
    def _result(
        self,
        mode: int,
        mad_code: str | None,
        policy_m3: bool = True,
        policy_m4: bool = True,
        hitl_continue: bool | None = None,
        error: str | None = None,
    ) -> dict[str, Any]:
        r: dict[str, Any] = {
            "mode": mode,
            "policy": {
                "mode": mode,
                "policy_m0": False,
                "policy_m2": False,
                "policy_m3": policy_m3,
                "policy_m4": policy_m4,
            },
            "verdict": {
                "event_id": "ev-1",
                "mad_code": mad_code,
            }
            if mad_code is not None
            else None,
            "error": error,
        }
        if hitl_continue is not None and r["verdict"]:
            r["verdict"]["hitl_continue"] = hitl_continue
        return r

    # --- MODE_BLOCK ---

    def test_block_mode_m0_allow(self) -> None:
        r = self._result(pb.MODE_BLOCK, "M0")
        assert _verdict_action(r) == "allow"

    def test_block_mode_m2_no_policy_allows(self) -> None:
        # Strictly policy-driven: M2 with policy_m2 off is not acted on.
        r = self._result(pb.MODE_BLOCK, "M2.a", policy_m3=True, policy_m4=True)
        assert _verdict_action(r) == "allow"

    def test_block_mode_m2_with_policy_blocks(self) -> None:
        # policy_m2 on in block mode → hard block (uniform with M3/M4).
        r = self._result(pb.MODE_BLOCK, "M2.a")
        r["policy"]["policy_m2"] = True
        assert _verdict_action(r) == "block"

    def test_block_mode_m0_with_policy_blocks(self) -> None:
        # policy_m0 on in block mode → block (the dashboard "act on M0" toggle).
        r = self._result(pb.MODE_BLOCK, "M0")
        r["policy"]["policy_m0"] = True
        assert _verdict_action(r) == "block"

    def test_block_mode_m3_block(self) -> None:
        r = self._result(pb.MODE_BLOCK, "M3.f")
        assert _verdict_action(r) == "block"

    def test_block_mode_m4_block(self) -> None:
        r = self._result(pb.MODE_BLOCK, "M4.b")
        assert _verdict_action(r) == "block"

    def test_block_mode_m3_policy_off_allows(self) -> None:
        # Strictly policy-driven: disabling policy_m3 stops enforcement
        # (logged as "not enforced"), no hard-wired M3 block.
        r = self._result(pb.MODE_BLOCK, "M3.f", policy_m3=False)
        assert _verdict_action(r) == "allow"

    # --- MODE_ALERT ---

    def test_alert_mode_always_allow(self) -> None:
        r = self._result(pb.MODE_ALERT, "M3.f")
        assert _verdict_action(r) == "allow"

    def test_alert_mode_m4_allow(self) -> None:
        r = self._result(pb.MODE_ALERT, "M4.b")
        assert _verdict_action(r) == "allow"

    # --- MODE_HITL (prompts user in Claude Code terminal) ---

    def test_hitl_m3_prompts_user(self) -> None:
        r = self._result(pb.MODE_HITL, "M3.f")
        assert _verdict_action(r) == "ask"

    def test_hitl_m4_prompts_user(self) -> None:
        r = self._result(pb.MODE_HITL, "M4.b")
        assert _verdict_action(r) == "ask"

    def test_hitl_m2_no_policy_allows(self) -> None:
        # Strictly policy-driven: HITL M2 with policy_m2 off is not acted on.
        r = self._result(pb.MODE_HITL, "M2.a")
        assert _verdict_action(r) == "allow"

    def test_hitl_m2_with_policy_asks(self) -> None:
        r = self._result(pb.MODE_HITL, "M2.a")
        r["policy"]["policy_m2"] = True
        assert _verdict_action(r) == "ask"

    def test_hitl_m3_policy_off_allows(self) -> None:
        # HITL honors the flag too: policy_m3 off → allow (no hard-wired ask).
        r = self._result(pb.MODE_HITL, "M3.f", policy_m3=False)
        assert _verdict_action(r) == "allow"

    def test_hitl_m0_allow(self) -> None:
        r = self._result(pb.MODE_HITL, "M0")
        assert _verdict_action(r) == "allow"

    # --- MODE_DEFER_HITL (verdict comes back, prompt user for M3/M4) ---

    def test_defer_hitl_m3_ask(self) -> None:
        r = self._result(MODE_DEFER_HITL, "M3.f")
        assert _verdict_action(r) == "ask"

    def test_defer_hitl_m4_ask(self) -> None:
        r = self._result(MODE_DEFER_HITL, "M4.b")
        assert _verdict_action(r) == "ask"

    def test_defer_hitl_m0_allow(self) -> None:
        r = self._result(MODE_DEFER_HITL, "M0")
        assert _verdict_action(r) == "allow"

    def test_defer_hitl_m2_with_policy(self) -> None:
        r = self._result(MODE_DEFER_HITL, "M2.a")
        r["policy"]["policy_m2"] = True
        # In DEFER_HITL, policy_m2=true means "ask" (prompt user), not "audit".
        assert _verdict_action(r) == "ask"

    def test_defer_hitl_timeout_asks(self) -> None:
        r = self._result(MODE_DEFER_HITL, None, error="verdict_timeout")
        assert _verdict_action(r) == "ask"

    # --- Timeout / Error ---

    def test_timeout_fail_open(self) -> None:
        r = self._result(pb.MODE_BLOCK, None, error="verdict_timeout")
        # FAIL_OPEN is True by default.
        assert _verdict_action(r) in ("allow", "block")

    def test_no_verdict_no_error(self) -> None:
        r: dict[str, Any] = {
            "mode": pb.MODE_BLOCK,
            "policy": {},
            "verdict": None,
            "error": None,
        }
        action = _verdict_action(r)
        assert action in ("allow", "block")

    # --- Edge cases ---

    def test_empty_mad_code(self) -> None:
        r = self._result(pb.MODE_BLOCK, "")
        assert _verdict_action(r) == "allow"

    def test_unknown_mad_code(self) -> None:
        r = self._result(pb.MODE_BLOCK, "M99.x")
        assert _verdict_action(r) == "allow"

    def test_case_insensitive(self) -> None:
        r = self._result(pb.MODE_BLOCK, "m3.f")
        assert _verdict_action(r) == "block"

    def test_m2_with_policy_on(self) -> None:
        # policy_m2 on in block mode → block (was "audit" pre strict-policy).
        r = self._result(pb.MODE_BLOCK, "M2.c")
        r["policy"]["policy_m2"] = True
        assert _verdict_action(r) == "block"
