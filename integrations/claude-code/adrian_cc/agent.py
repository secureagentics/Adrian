#!/usr/bin/env python3
"""Adrian CC Plugin - Claude Code hook handler.

Each hook invocation:
  1. Reads hook JSON from stdin
  2. Opens a direct WebSocket to the Adrian backend
  3. Sends SessionLogin + PairedEvent (protobuf)
  4. Handles the verdict according to the org's execution mode:
     - MODE_ALERT : fire-and-forget, never block
     - MODE_BLOCK : wait for verdict, block M3/M4 per policy
     - MODE_HITL  : wait for human approval on the dashboard
  5. Returns allow / deny / advisory JSON to Claude Code

Modes are server-driven via PolicySnapshot on LoginAck.  The hook
agent does not decide the mode - it reads it from the backend.
"""

from __future__ import annotations

import asyncio
import json
import os
import ssl
import sys
import tempfile
import time
from collections.abc import Callable
from contextlib import contextmanager, suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast
from uuid import uuid4

import websockets

# certifi supplies an up-to-date CA bundle (vendored under vendor/) used to
# supplement the OS trust store for wss:// verification. Optional: without it
# the OS store is used alone.
try:
    import certifi
except ImportError:  # pragma: no cover - certifi is vendored, import guarded
    certifi = None

# Cross-platform file locking: fcntl on POSIX, msvcrt on Windows. Exactly one
# is present on any given OS; the lock is tied to an open fd so the OS releases
# it automatically if the process dies (no stale-lock deadlock).
try:
    import fcntl  # POSIX advisory file locking
except ImportError:  # pragma: no cover - non-POSIX
    fcntl = None
try:
    import msvcrt  # Windows byte-range file locking
except ImportError:  # pragma: no cover - POSIX
    msvcrt = None

from adrian_cc.proto import event_pb2 as pb
from adrian_cc.transcript import (
    get_invocation_id,
    get_latest_reasoning,
    get_system_prompt,
    get_user_instruction,
    parse_transcript,
)

# ---------------------------------------------------------------------------
# .env loader
# ---------------------------------------------------------------------------


def _load_dotenv() -> None:
    """Load ADRIAN_* config from .env files into the environment.

    Precedence, highest to lowest: a variable already set in the environment,
    then the project-local ``$CWD/.env``, then ``~/.adrian/.env`` as a global
    fallback. Both files are read (the global fills only the keys a project
    .env omits, rather than being skipped whenever a project .env exists), and
    only ``ADRIAN_``-prefixed keys are ingested so a project's unrelated .env
    values never leak into this process.
    """
    # Project-local first, then the global fallback: the first file to set a
    # key wins, so a project .env overrides ~/.adrian/.env, and an existing
    # environment variable overrides both.
    for candidate in [Path.cwd() / ".env", Path.home() / ".adrian" / ".env"]:
        if not candidate.is_file():
            continue
        with open(candidate) as f:
            for line in f:
                line = line.strip()
                if not line or line.startswith("#") or "=" not in line:
                    continue
                k, _, v = line.partition("=")
                k, v = k.strip(), v.strip()
                if k.startswith("ADRIAN_") and k not in os.environ:
                    os.environ[k] = v


_load_dotenv()


# ---------------------------------------------------------------------------
# Config
# ---------------------------------------------------------------------------

ADRIAN_WS_URL = os.getenv("ADRIAN_WS_URL", "ws://localhost:8080/ws")
ADRIAN_API_KEY = os.getenv("ADRIAN_API_KEY", "")
VERDICT_TIMEOUT = float(os.getenv("ADRIAN_CC_VERDICT_TIMEOUT", "15"))
FAIL_OPEN = os.getenv("ADRIAN_CC_FAIL_OPEN", "true").lower() in ("true", "1", "yes")

# In server HITL mode, the server withholds M3/M4 verdicts.
HITL_TIMEOUT = float(os.getenv("ADRIAN_CC_HITL_TIMEOUT", "8"))

# Hard outer ceiling on the whole send→verdict round-trip. A safety net: even if
# a phase wedges past its per-phase timeout (connect/login/recv/close), the hook
# still returns and the process exits promptly rather than lingering up to Claude
# Code's hook budget (600s for a command PreToolUse) - and, once this is no
# longer run as a CC-reaped hook (shared wire layer / daemon), nothing relies on
# CC killing us. Kept above the per-phase timeouts so it never cuts a legitimate
# verdict wait. Override via ADRIAN_CC_MAX_HOOK_SECONDS.
_env_max = os.getenv("ADRIAN_CC_MAX_HOOK_SECONDS")
MAX_HOOK_TIMEOUT = (
    float(_env_max) if _env_max else max(VERDICT_TIMEOUT, HITL_TIMEOUT) + 20.0
)

# MODE_DEFER_HITL = 4 - new mode added to the proto. The old compiled
# proto doesn't have the enum name but protobuf handles it as int 4.
# We define the constant here for forward-compat.
MODE_DEFER_HITL = 4

GOVERNANCE_CONTEXT = "\n".join(
    [
        "Adrian runtime security is active for this session.",
        "Every tool call is classified in real-time (M0=benign, M2=audit, M3/M4=blocked).",
        "All events are logged to the Adrian dashboard.",
    ]
)

# Persistent state across hook calls within a session.
_STATE_DIR = Path.home() / ".adrian"
_STATE_FILE = _STATE_DIR / "cc-state.json"
_STATE_LOCK = _STATE_DIR / "adrian-cc-state.lock"


# ---------------------------------------------------------------------------
# Logging / output helpers
# ---------------------------------------------------------------------------


def _mode_name(mode: int) -> str:
    """Safe enum name resolution - handles MODE_DEFER_HITL and future modes."""
    _NAMES = {0: "UNSPECIFIED", 1: "ALERT", 2: "HITL", 3: "BLOCK", 4: "DEFER_HITL"}
    return _NAMES.get(mode, f"UNKNOWN({mode})")


def _ws_ssl_context() -> ssl.SSLContext | None:
    """TLS context for wss:// connections; None for ws:// (no TLS).

    The OS/default trust store is primary: it carries enterprise or custom
    roots (TLS-inspection proxies, internal CAs) plus OS-maintained public
    roots. certifi's bundle (vendored under vendor/) is added on top, never
    replacing the OS roots, so an incomplete store (e.g. a locked-down Windows
    image missing a recent root, where Python's ssl does no browser-style AIA
    fetching) still verifies public CAs. If certifi is absent, the OS store is
    used alone.
    """
    if not ADRIAN_WS_URL.lower().startswith("wss://"):
        return None
    ctx = ssl.create_default_context()
    if certifi is not None:
        with suppress(Exception):
            ctx.load_verify_locations(cafile=certifi.where())
    return ctx


def _log(msg: str) -> None:
    sys.stderr.write(f"[adrian-cc] {msg}\n")


def _exit_json(payload: dict[str, Any]) -> None:
    print(json.dumps(payload), flush=True)
    sys.exit(0)


def _exit_allow() -> None:
    sys.exit(0)


# ---------------------------------------------------------------------------
# Session state (agent hierarchy, invocation tracking)
# ---------------------------------------------------------------------------

# Max time to wait for the state lock before proceeding WITHOUT it. The
# critical section (load + mutate a small dict + atomic save) is sub-ms, so
# real contention clears near-instantly; the ceiling only guards against a
# pathologically stuck peer. Degrading to an unlocked write is preferable to
# hanging the hook (which would stall Claude until CC's 60s hook timeout).
_LOCK_TIMEOUT = 5.0
_LOCK_POLL = 0.02


def _try_lock(fd: int) -> bool:
    """Try to take a non-blocking exclusive lock on an open fd.

    Returns True if acquired, False if another process holds it. Portable:
    fcntl (POSIX) / msvcrt (Windows). True when no primitive exists (caller
    proceeds unlocked).
    """
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False  # held by another process (EAGAIN / EACCES)


def _unlock(fd: int) -> None:
    try:
        if fcntl is not None:
            fcntl.flock(fd, fcntl.LOCK_UN)
        elif msvcrt is not None:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


@contextmanager
def _state_lock():
    """Exclusive cross-process lock around a state read-modify-write.

    Claude Code fires PreToolUse/PostToolUse hooks CONCURRENTLY for parallel
    tool calls, so each hook is a separate process racing on cc-state.json.
    This serialises the load->mutate->save critical section across processes on
    POSIX (fcntl) and Windows (msvcrt) alike. The lock is bound to an open fd,
    so the OS drops it automatically if a holder crashes - no stale lock file.

    Acquisition spins non-blockingly up to _LOCK_TIMEOUT, then degrades to an
    unlocked run rather than hanging the hook. Also a no-op if neither locking
    primitive nor the lock file is available.
    """
    if fcntl is None and msvcrt is None:
        yield
        return
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd = os.open(str(_STATE_LOCK), os.O_CREAT | os.O_RDWR, 0o600)
    except OSError:
        yield  # can't create the lock file - degrade to no lock rather than fail
        return
    acquired = False
    try:
        deadline = time.monotonic() + _LOCK_TIMEOUT
        while not (acquired := _try_lock(fd)):
            if time.monotonic() >= deadline:
                break  # give up waiting; proceed unlocked rather than hang
            time.sleep(_LOCK_POLL)
        yield
    finally:
        if acquired:
            _unlock(fd)
        os.close(fd)


def _load_state() -> dict[str, Any]:
    try:
        if _STATE_FILE.exists():
            return json.loads(_STATE_FILE.read_text())
    except Exception:
        pass
    return {
        "agent_stack": [{"agent_id": "claude-code", "spawn_id": ""}],
        "invocation_count": 0,
    }


def _atomic_replace(src: str, dst: Path) -> None:
    """os.replace(src, dst) with a short retry on Windows PermissionError.

    On POSIX, replace over an open file is atomic and never blocks. On Windows,
    it raises PermissionError if a concurrent reader (an unlocked _load_state)
    momentarily holds the destination open; the reader's open is brief, so a
    few short retries clear it. POSIX re-raises immediately (no such case).
    """
    for _ in range(20):
        try:
            os.replace(src, dst)
            return
        except PermissionError:
            if os.name != "nt":
                raise
            time.sleep(_LOCK_POLL)
    os.replace(src, dst)  # last attempt - let it raise if still contended


def _save_state(state: dict[str, Any]) -> None:
    """Atomically write state to disk.

    Write a temp file then replace onto the target, so a concurrent reader
    never sees a truncated/partial file.
    """
    try:
        _STATE_DIR.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            dir=str(_STATE_DIR), prefix=".cc-state-", suffix=".tmp"
        )
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(state, f)
            _atomic_replace(tmp, _STATE_FILE)
        except Exception:
            with suppress(OSError):
                os.unlink(tmp)
            raise
    except Exception:
        pass


def _mutate_state[T](fn: Callable[[dict[str, Any]], T]) -> T:
    """Run a locked read-modify-write over the persistent state.

    Acquire the lock, load, apply fn(state), save atomically, release.
    Returns whatever fn returns.
    """
    with _state_lock():
        state = _load_state()
        result = fn(state)
        _save_state(state)
        return result


def _reset_state() -> None:
    _mutate_state(
        lambda s: s.update(
            {
                "agent_stack": [{"agent_id": "claude-code", "spawn_id": ""}],
                "invocation_count": 0,
            }
        )
    )


def _current_agent(state: dict[str, Any]) -> str:
    stack = state.get("agent_stack", [{"agent_id": "claude-code"}])
    return stack[-1]["agent_id"]


def _parent_agent(state: dict[str, Any]) -> str:
    stack = state.get("agent_stack", [])
    return stack[-2]["agent_id"] if len(stack) >= 2 else ""


def _push_subagent(
    state: dict[str, Any], tool_use_id: str, tool_input: dict[str, Any]
) -> None:
    """Record a delegation frame when the parent invokes the Agent tool.

    Used for agent-hierarchy (parent/child) tracking. The delegated PROMPT is
    not stored here - it's recovered deterministically per agent_id from the
    sub-agent's own transcript (see _subagent_delegated_prompt).
    """
    sub_type = tool_input.get("subagent_type", "subagent")
    desc = tool_input.get("description", "")
    slug = desc.lower().replace(" ", "-")[:30].rstrip("-") if desc else ""
    agent_id = f"{sub_type}:{slug}" if slug else sub_type
    state.setdefault("agent_stack", []).append(
        {
            "agent_id": agent_id,
            "spawn_id": tool_use_id,
            "subagent_type": sub_type,
            "description": desc,
        }
    )


def _blocks(content: Any) -> list[Any]:
    """The block list of a message ``content`` value, or [] when not a list."""
    if isinstance(content, list):
        return cast(list[Any], content)
    return []


def _field(block: Any, key: str) -> str:
    """A string field from a dict content block; '' when block is not a dict."""
    if isinstance(block, dict):
        return cast(dict[str, Any], block).get(key, "")
    return ""


def _subagent_delegated_prompt(transcript_path: str, agent_id: str) -> str:
    """Recover a sub-agent's delegated prompt from its own transcript.

    Keyed by agent_id with no ordering assumptions, so it is correct for any
    number of concurrently-spawned sub-agents. The sub-agent's transcript is
    named after its agent_id
    (``<session>/subagents/agent-<agent_id>.jsonl``) and its first ``user``
    message is the parent's delegation instruction (verified present by the
    sub-agent's first tool call). Returns '' if unavailable.
    """
    if not transcript_path or not agent_id:
        return ""
    try:
        sub = (
            Path(transcript_path).with_suffix("")
            / "subagents"
            / f"agent-{agent_id}.jsonl"
        )
        if not sub.is_file():
            return ""
        with open(sub, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if entry.get("type") != "user":
                    continue
                content = entry.get("message", {}).get("content", "")
                if isinstance(content, str) and content:
                    return content
                parts = [
                    _field(b, "text")
                    for b in _blocks(content)
                    if _field(b, "type") == "text"
                ]
                joined = " ".join(p for p in parts if p)
                if joined:
                    return joined
    except Exception:
        return ""
    return ""


def _pop_subagent(state: dict[str, Any], tool_use_id: str) -> None:
    stack = state.get("agent_stack", [])
    if len(stack) > 1 and stack[-1].get("spawn_id") == tool_use_id:
        stack.pop()


# ---------------------------------------------------------------------------
# Event builder
# ---------------------------------------------------------------------------


def _build_event(
    hook_data: dict[str, Any],
    state: dict[str, Any],
    output: str = "",
    delegated_prompt: str = "",
) -> pb.PairedEvent:
    session_id = hook_data.get("session_id", "")
    tool_name = hook_data.get("tool_name", "")
    tool_input = hook_data.get("tool_input", {})
    tool_use_id = hook_data.get("tool_use_id", "")
    transcript_path = hook_data.get("transcript_path", "")

    # Claude Code provides native agent_id for subagent hooks.
    cc_agent_id = hook_data.get("agent_id", "")
    cc_agent_type = hook_data.get("agent_type", "")

    # Invocation ID - branching by agent thread.
    #
    # Root level (claude-code): hash(session + user_message_count)
    #   Groups all root-level tool calls within one user prompt.
    #
    # Sub-agent level: the tool_use_id of the Agent call that spawned
    #   this branch. Each parallel sub-agent gets its own invocation_id
    #   so events from concurrent agents are distinguishable.
    #
    # This creates a tree:
    #   Prompt → root_invocation
    #     ├── Agent(spawn_id=toolu_01) → invocation=toolu_01
    #     │     └── Grep/Read/etc → invocation=toolu_01
    #     └── Agent(spawn_id=toolu_02) → invocation=toolu_02
    #           └── Glob/Write/etc → invocation=toolu_02
    #
    stack = state.get("agent_stack", [{"agent_id": "claude-code", "spawn_id": ""}])
    # Prefer Claude Code's per-turn prompt_id: it's on every hook payload
    # (UserPromptSubmit/Pre/PostToolUse), identical to the transcript's promptId,
    # and one per user prompt - so it groups a prompt's tool calls without a
    # transcript read and is available even before the transcript is written
    # (e.g. at UserPromptSubmit). Fall back to the transcript-derived id.
    root_invocation = hook_data.get("prompt_id", "") or get_invocation_id(
        session_id, transcript_path
    )

    if len(stack) > 1 and stack[-1].get("spawn_id"):
        # Inside a sub-agent - use the spawn_id as the branch invocation.
        invocation_id = stack[-1]["spawn_id"]
    else:
        invocation_id = root_invocation

    # Agent hierarchy - prefer Claude Code's native agent_id if present,
    # fall back to our state-tracked hierarchy.
    if cc_agent_id:
        agent_id = f"{cc_agent_type}:{cc_agent_id}" if cc_agent_type else cc_agent_id
        parent_id = _current_agent(state) if cc_agent_id != "claude-code" else ""
    else:
        agent_id = _current_agent(state)
        parent_id = _parent_agent(state)

    # Transcript context.
    system_prompt = get_system_prompt(transcript_path)
    user_instruction = get_user_instruction(transcript_path)
    reasoning = get_latest_reasoning(transcript_path, max_len=4096)

    # Serialize inputs.
    input_str = json.dumps(tool_input, default=str) if tool_input else ""

    # parent_run_id links sub-agent branches back to the root invocation.
    # For sub-agents: points to the root invocation_id.
    # For root: empty (no parent).
    parent_run_id = root_invocation if invocation_id != root_invocation else ""

    # Build protobuf.
    event = pb.PairedEvent(
        event_id=str(uuid4()),
        invocation_id=invocation_id,
        session_id=session_id,
        run_id=tool_use_id or str(uuid4()),
        parent_run_id=parent_run_id,
        timestamp=datetime.now(UTC).isoformat(),
        pair_type=pb.PAIR_TYPE_TOOL,
    )

    # Agent context. For a sub-agent event, prefer the delegated prompt (the
    # instruction the parent gave it via the Agent tool) as its system prompt,
    # since CC's real system prompt isn't exposed. Fall back to the transcript's
    # system prompt. Do NOT fall back to reasoning: it's a prior turn's thinking,
    # not a system prompt, and mislabelling it here pollutes the classifier's
    # agent context. Reasoning is still surfaced in metadata.reasoning_latest.
    event.agent.agent_id = agent_id
    event.agent.system_prompt = delegated_prompt or system_prompt
    event.agent.user_instruction = user_instruction

    # Parent context.
    event.parent.agent_id = parent_id
    if parent_id:
        event.parent.user_instruction = user_instruction

    # Tool data.
    event.tool.tool_name = tool_name
    event.tool.tool_call_id = tool_use_id or ""
    event.tool.input = input_str
    event.tool.output = output

    # Metadata: full context for the dashboard.
    transcript_state = parse_transcript(transcript_path)
    all_reasoning = transcript_state.reasoning_blocks

    metadata = {
        "source": "claude-code",
        "hook_type": hook_data.get("hook_event_name", ""),
        "cwd": hook_data.get("cwd", ""),
        "permission_mode": hook_data.get("permission_mode", ""),
        "reasoning_block_count": len(all_reasoning),
        "reasoning_latest": reasoning[:2048] if reasoning else "",
        "cc_agent_id": cc_agent_id,
        "cc_agent_type": cc_agent_type,
        # Branch tracking for parallel agent trees.
        "root_invocation_id": root_invocation,
        "branch_depth": len(stack) - 1,  # 0 = root, 1 = sub-agent, 2 = nested
        "branch_spawn_id": stack[-1].get("spawn_id", "") if len(stack) > 1 else "",
    }
    event.metadata_json = json.dumps(metadata, default=str).encode()

    return event


# ---------------------------------------------------------------------------
# WebSocket client - connect, login, send, wait for verdict
# ---------------------------------------------------------------------------


async def _ws_send_event(
    event: pb.PairedEvent,
    session_id: str,
    wait_for_verdict: bool = True,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Send an event to Adrian backend and optionally wait for verdict.

    Returns a dict with keys:
      - mode: int (MODE_ALERT/BLOCK/HITL)
      - policy: PolicySnapshot or None
      - verdict: dict with mad_code, event_id, hitl fields (or None)
      - error: str (if something failed)
    """
    result: dict[str, Any] = {
        "mode": pb.MODE_UNSPECIFIED,
        "policy": None,
        "verdict": None,
        "error": None,
    }

    headers: dict[str, str] = {}
    if ADRIAN_API_KEY:
        headers["Authorization"] = f"Bearer {ADRIAN_API_KEY}"

    # Per-connection routing id: unique per WS connection. Claude Code fires
    # parallel tool-call hooks that all share one CC session_id; without a
    # distinct id per connection the server evicts each previous connection
    # and verdicts get dropped/misrouted. Stamped on both the SessionLogin
    # and the event so the server routes and the engine publishes this
    # connection's verdict on verdicts.<connection_id>. session_id is left
    # untouched so it still keys the engine's context window + storage.
    connection_id = str(uuid4())
    event.connection_id = connection_id

    try:
        async with websockets.connect(
            ADRIAN_WS_URL,
            additional_headers=headers,
            ssl=_ws_ssl_context(),
            open_timeout=5,
            close_timeout=3,
        ) as ws:
            # --- Login ---
            login = pb.SessionLogin(
                session_id=session_id,
                schema_version=2,
                source="claude-code",
                connection_id=connection_id,
            )
            login.llm_stack.provider = "anthropic"
            login.llm_stack.model = "claude-code"
            await ws.send(pb.ClientFrame(login=login).SerializeToString())

            # --- LoginAck ---
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            sf = pb.ServerFrame()
            sf.ParseFromString(raw if isinstance(raw, bytes) else raw.encode())

            if sf.WhichOneof("frame") == "login_ack":
                policy = sf.login_ack.policy
                result["mode"] = policy.mode
                result["policy"] = {
                    "mode": policy.mode,
                    "mode_name": _mode_name(policy.mode),
                    "policy_m0": policy.policy_m0,
                    "policy_m2": policy.policy_m2,
                    "policy_m3": policy.policy_m3,
                    "policy_m4": policy.policy_m4,
                }
                result["source_ack"] = sf.login_ack.source

            # --- Send event ---
            batch = pb.PairedEventBatch(events=[event])
            await ws.send(pb.ClientFrame(paired_batch=batch).SerializeToString())

            if not wait_for_verdict:
                return result

            # --- MODE_ALERT: server won't send verdicts ---
            if result["mode"] == pb.MODE_ALERT:
                return result

            # --- Wait for verdict ---
            effective_timeout = timeout
            if effective_timeout is None:
                if result["mode"] == pb.MODE_HITL:
                    effective_timeout = HITL_TIMEOUT
                else:
                    effective_timeout = VERDICT_TIMEOUT

            raw = await asyncio.wait_for(ws.recv(), timeout=effective_timeout)
            sf2 = pb.ServerFrame()
            sf2.ParseFromString(raw if isinstance(raw, bytes) else raw.encode())

            if sf2.WhichOneof("frame") == "verdict":
                v = sf2.verdict
                verdict_dict: dict[str, Any] = {
                    "event_id": v.event_id,
                    "session_id": v.session_id,
                    "mad_code": v.mad_code,
                }
                # HITL resolution.
                if v.HasField("hitl"):
                    verdict_dict["hitl_continue"] = v.hitl.continue_execution
                # Policy snapshot on the verdict.
                if v.HasField("policy"):
                    verdict_dict["verdict_mode"] = v.policy.mode
                    verdict_dict["verdict_mode_name"] = _mode_name(v.policy.mode)
                    verdict_dict["policy_m3"] = v.policy.policy_m3
                    verdict_dict["policy_m4"] = v.policy.policy_m4

                result["verdict"] = verdict_dict

    except TimeoutError:
        result["error"] = "verdict_timeout"
    except Exception as exc:
        result["error"] = (
            f"{type(exc).__name__}: {exc}" if str(exc) else type(exc).__name__
        )

    return result


def _send_event_sync(
    event: pb.PairedEvent,
    session_id: str,
    wait_for_verdict: bool = True,
    timeout: float | None = None,
) -> dict[str, Any]:
    """Run the WS round-trip under a hard MAX_HOOK_TIMEOUT ceiling.

    On timeout the inner coroutine is cancelled; _ws_send_event's
    ``async with websockets.connect(...)`` closes the socket on that (and every)
    exit path - CancelledError is a BaseException, so the coroutine's broad
    ``except Exception`` never swallows the cancellation. asyncio.run then tears
    down the loop, leaving no dangling task or connection. Always returns a
    result dict (never raises) so the caller's policy decision still runs.
    """

    async def _bounded() -> dict[str, Any]:
        return await asyncio.wait_for(
            _ws_send_event(event, session_id, wait_for_verdict, timeout),
            timeout=MAX_HOOK_TIMEOUT,
        )

    try:
        return asyncio.run(_bounded())
    except TimeoutError:
        return {
            "mode": pb.MODE_UNSPECIFIED,
            "policy": None,
            "verdict": None,
            "error": "max_hook_timeout",
        }
    except Exception as exc:  # never let transport teardown crash the hook
        return {
            "mode": pb.MODE_UNSPECIFIED,
            "policy": None,
            "verdict": None,
            "error": f"{type(exc).__name__}: {exc}",
        }


async def _verify_connection() -> dict[str, Any]:
    """Connect and SessionLogin only, with no event and no verdict.

    Confirms the URL is reachable and the API key authenticates. Returns
    {ok, source_ack, mode_name} or {ok: False, error}. Never touches the key
    value beyond the auth header.
    """
    headers: dict[str, str] = {}
    if ADRIAN_API_KEY:
        headers["Authorization"] = f"Bearer {ADRIAN_API_KEY}"
    try:
        async with websockets.connect(
            ADRIAN_WS_URL,
            additional_headers=headers,
            ssl=_ws_ssl_context(),
            open_timeout=5,
            close_timeout=3,
        ) as ws:
            login = pb.SessionLogin(
                session_id=f"verify-{uuid4().hex[:8]}",
                schema_version=2,
                source="claude-code",
            )
            login.llm_stack.provider = "anthropic"
            login.llm_stack.model = "claude-code"
            await ws.send(pb.ClientFrame(login=login).SerializeToString())
            raw = await asyncio.wait_for(ws.recv(), timeout=5)
            sf = pb.ServerFrame()
            sf.ParseFromString(raw if isinstance(raw, bytes) else raw.encode())
            if sf.WhichOneof("frame") == "login_ack":
                return {
                    "ok": True,
                    "source_ack": sf.login_ack.source,
                    "mode_name": _mode_name(sf.login_ack.policy.mode),
                }
            return {"ok": False, "error": "server did not send a LoginAck"}
    except Exception as exc:
        return {"ok": False, "error": f"{type(exc).__name__}: {exc}".strip(": ")}


def _handle_verify() -> None:
    """Run the ``adrian-cc verify`` CLI subcommand (not a hook).

    Used by /adrian-init to confirm the backend URL + API key. Prints a
    human-readable OK/FAIL line only, never the key. Exit 0 on success, 1 on
    failure.
    """
    if not ADRIAN_API_KEY:
        print("FAIL: ADRIAN_API_KEY is not set - add it to ~/.adrian/.env")
        sys.exit(1)
    res = asyncio.run(_verify_connection())
    if res.get("ok"):
        print(
            f"OK: connected to {ADRIAN_WS_URL} "
            f"(source_ack={res.get('source_ack')!r}, backend mode={res.get('mode_name')})"
        )
        sys.exit(0)
    print(
        f"FAIL: {res.get('error')} (check ADRIAN_WS_URL and ADRIAN_API_KEY in ~/.adrian/.env)"
    )
    sys.exit(1)


# ---------------------------------------------------------------------------
# Verdict → action mapping (mode-aware)
# ---------------------------------------------------------------------------


def _code_enabled(mad_code: str, policy: dict[str, Any]) -> bool:
    """Whether the org policy is configured to act on this MAD code.

    Strictly policy-driven: a code triggers the active mode's action iff its
    ``policy_m*`` flag is set. No code is hard-wired, so disabling a flag
    (e.g. ``policy_m4``) genuinely stops enforcement for that code. Empty or
    unrecognised codes return False (allow), matching the prior fall-through.
    """
    # policy_m0..m4, keyed by the MAD digit; empty/unknown → absent key → False.
    return bool(policy.get(f"policy_m{(mad_code or '')[1:2]}", False))


def _verdict_action(result: dict[str, Any]) -> str:
    """Determine the action for a verdict result.

    Returns:
        "allow"  - silent pass
        "block"  - hard deny, tool does not execute
        "ask"    - prompt the user in Claude Code's terminal to confirm

    Strictly policy-driven (server-driven via LoginAck PolicySnapshot): a MAD
    code triggers the mode's action iff its policy_m* flag is enabled; the mode
    only chooses the action. No code is hard-wired.
        MODE_ALERT (1)      : never enforce client-side (server drops verdicts)
        MODE_HITL (2)       : enabled code → "ask" (native CLI approval)
        MODE_BLOCK (3)      : enabled code → "block" (hard deny)
        MODE_DEFER_HITL (4) : enabled code → "ask"
    """
    mode = result.get("mode", pb.MODE_UNSPECIFIED)
    verdict = result.get("verdict")
    error = result.get("error")

    # ---- No verdict (timeout or error) ----
    if verdict is None:
        # MODE_HITL: server withholds in-scope verdicts → timeout = flagged.
        if mode == pb.MODE_HITL and error == "verdict_timeout":
            _log("HITL: verdict withheld by server - prompting user")
            return "ask"
        # MODE_DEFER_HITL: verdicts should arrive (like BLOCK), timeout = error.
        if mode == MODE_DEFER_HITL and error == "verdict_timeout":
            _log("DEFER_HITL: verdict timeout - prompting user as fallback")
            return "ask"
        if error == "verdict_timeout":
            _log(f"Verdict timeout (mode={_mode_name(mode)})")
        elif error:
            _log(f"Error: {error}")
        return "allow" if FAIL_OPEN else "block"

    mad = (verdict.get("mad_code") or "").upper()
    policy = result.get("policy", {})

    # ---- MODE_ALERT: never enforce client-side (server drops verdicts) ----
    if mode == pb.MODE_ALERT:
        return "allow"

    # Strictly policy-driven for every enforcing mode: act on a code iff its
    # policy_m* flag is enabled. The mode picks the action (ask vs block).
    if not _code_enabled(mad, policy):
        # Security-relevant: an M3/M4 is passing because the org disabled its
        # flag. Surface it in logs so a mis-set policy is visible.
        if mad.startswith("M3") or mad.startswith("M4"):
            _log(f"{mad} not enforced: org policy flag is off")
        return "allow"

    if mode in (pb.MODE_HITL, MODE_DEFER_HITL):
        return "ask"
    if mode == pb.MODE_BLOCK:
        return "block"

    # Unknown / unspecified mode: default to allow.
    return "allow"


# ---------------------------------------------------------------------------
# Hook handlers
# ---------------------------------------------------------------------------


def _handle_start(_hook_data: dict[str, Any]) -> None:
    """SessionStart: reset state, inject governance context."""
    _reset_state()
    _exit_json(
        {
            "hookSpecificOutput": {
                "hookEventName": "SessionStart",
                "additionalContext": GOVERNANCE_CONTEXT,
            }
        }
    )


def _handle_pre(hook_data: dict[str, Any]) -> None:
    """PreToolUse: send event, handle verdict based on mode."""
    tool_name = hook_data.get("tool_name", "")
    tool_use_id = hook_data.get("tool_use_id", "")
    session_id = hook_data.get("session_id", "")
    cc_agent_id = hook_data.get("agent_id", "")
    transcript_path = hook_data.get("transcript_path", "")

    # Recover this sub-agent's delegated prompt deterministically from its own
    # transcript (keyed by agent_id → correct for any number of concurrent
    # sub-agents). Applies to every event a sub-agent produces - including when
    # it delegates onward via its own Agent call (nested case) - so the context
    # is consistent across pre/post. Empty for the main agent (no delegator).
    delegated_prompt = ""
    if cc_agent_id and cc_agent_id != "claude-code":
        delegated_prompt = _subagent_delegated_prompt(transcript_path, cc_agent_id)

    # Snapshot for building the event (before the delegation push, so an Agent
    # tool call stays attributed to the parent).
    state = _load_state()
    event = _build_event(hook_data, state, delegated_prompt=delegated_prompt)

    # On the Agent tool, record the delegation frame (with its prompt) under
    # the lock - after building the event.
    if tool_name == "Agent":
        ti = hook_data.get("tool_input", {})
        _mutate_state(lambda s: _push_subagent(s, tool_use_id, ti))

    # Send to backend.
    result = _send_event_sync(event, session_id, wait_for_verdict=True)
    action = _verdict_action(result)
    verdict: dict[str, Any] = result.get("verdict") or {}
    mad = verdict.get("mad_code", "")

    match action:
        case "block":
            # Hard deny - tool does not execute.
            blocked = _build_event(
                hook_data,
                state,
                output=f"[Blocked ({mad})]",
                delegated_prompt=delegated_prompt,
            )
            _send_event_sync(blocked, session_id, wait_for_verdict=False)

            _exit_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "deny",
                        "permissionDecisionReason": f"Adrian: Blocked ({mad})",
                    }
                }
            )

        case "ask":
            reason = f"Adrian HITL [{mad or 'pending'}]: {tool_name} - approve?"
            _exit_json(
                {
                    # Top-level systemMessage is a user-facing notice, separate from a
                    # tool's permission UI - unlike permissionDecisionReason (shown in
                    # the generic Bash confirm dialog but NOT in the Write/Edit/Read
                    # tool-specific dialogs), so it should surface the HITL notice
                    # across all tools.
                    "systemMessage": f"⚠️ Adrian HITL [{mad or 'pending'}] - approval required for {tool_name}",
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "permissionDecision": "ask",
                        "permissionDecisionReason": reason,
                        "additionalContext": f"⚠️ Adrian Security [{mad or 'pending'}] - HITL approval required for: {tool_name}",
                    },
                }
            )

        case "audit":
            _exit_json(
                {
                    "hookSpecificOutput": {
                        "hookEventName": "PreToolUse",
                        "additionalContext": f"Adrian advisory: {mad}",
                    }
                }
            )

        case _:
            _exit_allow()


def _handle_post(hook_data: dict[str, Any]) -> None:
    """PostToolUse: log tool output, pop sub-agent on Agent completion."""
    tool_name = hook_data.get("tool_name", "")
    tool_use_id = hook_data.get("tool_use_id", "")
    session_id = hook_data.get("session_id", "")
    cc_agent_id = hook_data.get("agent_id", "")
    transcript_path = hook_data.get("transcript_path", "")

    # Same deterministic prompt recovery for the sub-agent's post event.
    delegated_prompt = ""
    if cc_agent_id and cc_agent_id != "claude-code":
        delegated_prompt = _subagent_delegated_prompt(transcript_path, cc_agent_id)

    # Pop the delegation frame when the Agent tool completes (locked).
    if tool_name == "Agent":
        _mutate_state(lambda s: _pop_subagent(s, tool_use_id))

    state = _load_state()

    # Tool output: Claude Code sends it as "tool_result" (string).
    tool_result = hook_data.get("tool_result", "")
    # Fallback to tool_response (older format / our test format).
    if not tool_result:
        tool_response = hook_data.get("tool_response", {})
        tool_result = json.dumps(tool_response, default=str) if tool_response else ""

    event = _build_event(
        hook_data, state, output=tool_result, delegated_prompt=delegated_prompt
    )

    # Fire and forget - don't wait for verdict on post events.
    _send_event_sync(event, session_id, wait_for_verdict=False)
    _exit_allow()


def _handle_prompt(hook_data: dict[str, Any]) -> None:
    """UserPromptSubmit: capture the user's prompt for audit + classifier context.

    Fires when the user submits a message. We LOG the prompt (so the dashboard
    records it and the classifier sees the user's intent alongside the agent's
    tool calls) but NEVER block or reject it. Gating the human's own input isn't
    something hooks can do reliably, and it isn't Adrian's job - Adrian gates the
    agent's tool calls, not the user's messages. Fire-and-forget.
    """
    state = _load_state()
    session_id = hook_data.get("session_id", "")

    # Claude Code sends the user's prompt in the "prompt" field.
    prompt_content = hook_data.get("prompt", "")
    prompt_hook = {
        "session_id": session_id,
        "tool_name": "UserPrompt",
        "tool_input": {"message": prompt_content},
        "tool_use_id": f"prompt-{uuid4().hex[:8]}",
        "hook_event_name": "UserPromptSubmit",
        "cwd": hook_data.get("cwd", ""),
        "transcript_path": hook_data.get("transcript_path", ""),
        # Carry the per-turn prompt_id so this prompt event shares the same
        # invocation_id as the tool calls it triggers.
        "prompt_id": hook_data.get("prompt_id", ""),
    }

    event = _build_event(prompt_hook, state)
    # At UserPromptSubmit the prompt is in the payload but not yet in the
    # transcript, so set the user instruction directly from the payload.
    if not event.agent.user_instruction and prompt_content:
        event.agent.user_instruction = prompt_content

    # Capture only - fire-and-forget. Never block/reject the user's own input.
    _send_event_sync(event, session_id, wait_for_verdict=False)
    _exit_allow()


def _handle_notify(hook_data: dict[str, Any]) -> None:
    """Notification: log Claude's notifications for audit trail."""
    state = _load_state()
    session_id = hook_data.get("session_id", "")

    notify_hook = {
        "session_id": session_id,
        "tool_name": "Notification",
        "tool_input": {"message": hook_data.get("message", "")},
        "tool_use_id": f"notify-{uuid4().hex[:8]}",
        "hook_event_name": "Notification",
        "cwd": hook_data.get("cwd", ""),
        "transcript_path": hook_data.get("transcript_path", ""),
    }

    event = _build_event(notify_hook, state)
    _send_event_sync(event, session_id, wait_for_verdict=False)
    _exit_allow()


def _handle_stop(hook_data: dict[str, Any]) -> None:
    """Stop: capture Claude's complete assistant message for the turn (audit).

    Fires once when Claude finishes a turn. `last_assistant_message` is the full
    final assistant text (the visible reply - NOT reasoning, which is redacted).
    Fire-and-forget: the turn is already done, so there is nothing to gate; we
    log the AI side so the dashboard/classifier sees both halves of the
    conversation. Keyed by `prompt_id` so it shares the turn's invocation_id
    with that turn's user prompt and tool calls.
    """
    state = _load_state()
    session_id = hook_data.get("session_id", "")
    message = hook_data.get("last_assistant_message", "")
    if not message:
        _exit_allow()  # a turn with no assistant text - nothing to log

    stop_hook = {
        "session_id": session_id,
        "tool_name": "AssistantMessage",
        "tool_input": {"message": message},
        "tool_use_id": f"assistant-{uuid4().hex[:8]}",
        "hook_event_name": "Stop",
        "cwd": hook_data.get("cwd", ""),
        "transcript_path": hook_data.get("transcript_path", ""),
        "prompt_id": hook_data.get("prompt_id", ""),
    }

    event = _build_event(stop_hook, state)
    # Fire and forget - the assistant already spoke; we can't gate it, only log.
    _send_event_sync(event, session_id, wait_for_verdict=False)
    _exit_allow()


def _handle_end(_hook_data: dict[str, Any]) -> None:
    """SessionEnd: clean up state."""
    with suppress(Exception):
        _STATE_FILE.unlink(missing_ok=True)
    _exit_allow()


# ---------------------------------------------------------------------------
# Main
# ---------------------------------------------------------------------------


def main() -> None:
    """Entry point: dispatch the named hook (or the verify subcommand)."""
    event_type = sys.argv[1] if len(sys.argv) > 1 else "unknown"

    # `verify` is a CLI subcommand (used by /adrian-init), not a Claude Code
    # hook - it takes no stdin JSON and prints a human-readable result.
    if event_type == "verify":
        _handle_verify()
        return

    try:
        hook_data: dict[str, Any] = json.load(sys.stdin)
    except (json.JSONDecodeError, ValueError):
        hook_data = {}

    handlers = {
        "start": _handle_start,
        "prompt": _handle_prompt,
        "pre": _handle_pre,
        "post": _handle_post,
        "notify": _handle_notify,
        "stop": _handle_stop,
        "end": _handle_end,
    }
    handler = handlers.get(event_type)
    # Guaranteed shutdown: a handler ends by calling _exit_json/_exit_allow,
    # which raise SystemExit (a BaseException, so it propagates past the
    # except-Exception below). Any UNEXPECTED error fails open and still exits
    # cleanly, so the hook process never hangs or crashes - nothing dangling.
    try:
        if handler:
            handler(hook_data)
        _exit_allow()  # unknown event type, or handler returned without exiting
    except Exception as exc:
        _log(f"unexpected hook error, failing open: {exc!r}")
        _exit_allow()


if __name__ == "__main__":
    main()
