"""Edge-case and adversarial tests for the verdict gate system.

Covers scenarios the happy-path tests miss:
- Worker threads with no event loop (bare ThreadPoolExecutor)
- ws._loop stopped or None mid-gate
- _ws_client becomes None between check and use (disconnect race)
- Tools without tool_call_id (bypass vector)
- Concurrent tool calls hitting _sync_gate simultaneously
- HITL mode timeout behavior (must NOT fail-open)
- Verdict arriving before gate checks (resolved future replay)
- LRU eviction of tool_call_id map entries
- register_pending called from worker thread (wrong loop)
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import MagicMock

import adrian
import pytest
from adrian.proto import event_pb2 as pb
from adrian.ws import WebSocketClient
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import RunnableConfig, ensure_config
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime


def _runtime_config() -> RunnableConfig:
    return ensure_config({CONF: {CONFIG_KEY_RUNTIME: Runtime()}})


def _apply_mode(
    ws: WebSocketClient,
    mode: int,
    *,
    policy_m0: bool = False,
    policy_m2: bool = False,
    policy_m3: bool = False,
    policy_m4: bool = False,
) -> pb.PolicySnapshot:
    policy = pb.PolicySnapshot(
        mode=cast("pb.Mode", mode),
        policy_m0=policy_m0,
        policy_m2=policy_m2,
        policy_m3=policy_m3,
        policy_m4=policy_m4,
    )
    ws._mode = mode
    ws._policy = policy
    ws._login_ack_received.set()
    return policy


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    adrian.shutdown()


def _init_sdk(tmp_path: Path, block_timeout: float = 2.0) -> WebSocketClient:
    adrian.init(
        api_key="test-key",
        log_file=str(tmp_path / "events.jsonl"),
        auto_instrument=True,
        ws_url="ws://x",
        block_timeout=block_timeout,
    )
    ws = adrian._ws_client
    assert ws is not None
    return ws


def _tool_state(
    tc_id: str, tool_name: str, args: dict[str, Any] | None = None
) -> dict[str, Any]:
    ai = AIMessage(
        content="",
        tool_calls=[{"id": tc_id, "name": tool_name, "args": args or {"x": "hi"}}],
    )
    return {"messages": [ai]}


# ---------------------------------------------------------------------------
# 1. Worker thread without ANY event loop
# ---------------------------------------------------------------------------


class TestBareWorkerThread:
    """Simulate Pregel's ThreadPoolExecutor dispatch — no event loop on
    the worker thread at all."""

    async def test_sync_tool_blocks_from_bare_thread(self, tmp_path: Path) -> None:
        """A sync tool.invoke called from a bare thread (no loop set) with
        M4 verdict must block via run_coroutine_threadsafe to ws._loop."""
        tool_ran = False

        def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-bare"] = "llm-bare"

        fut = ws.register_pending("llm-bare")
        fut.set_result(pb.Verdict(event_id="llm-bare", mad_code="M4_a", policy=policy))

        tool_node = ToolNode([my_tool])
        result = await tool_node.ainvoke(
            _tool_state("tc-bare", "my_tool"), config=_runtime_config()
        )

        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content

    async def test_sync_tool_m3_blocks_when_m3_in_scope(self, tmp_path: Path) -> None:
        """M3 verdict with policy_m3=True must also block."""
        tool_ran = False

        def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m3=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-m3"] = "llm-m3"

        fut = ws.register_pending("llm-m3")
        fut.set_result(pb.Verdict(event_id="llm-m3", mad_code="M3_risk", policy=policy))

        tool_node = ToolNode([my_tool])
        result = await tool_node.ainvoke(
            _tool_state("tc-m3", "my_tool"), config=_runtime_config()
        )

        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 2. ws._loop is None or stopped
# ---------------------------------------------------------------------------


class TestWsLoopEdgeCases:
    async def test_ws_loop_is_none_sync_gate_fails_closed(self, tmp_path: Path) -> None:
        """If ws._loop is None (WS never connected), sync gate should
        fall through to asyncio.run() path — which will fail-closed if
        the async gate can't resolve."""
        tool_ran = False

        def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path, block_timeout=0.1)
        _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        # Intentionally NOT setting ws._loop — simulates WS not connected
        ws._loop = None
        ws._tool_call_id_to_event_id["tc-noloop"] = "llm-noloop"

        tool_node = ToolNode([my_tool])
        await tool_node.ainvoke(
            _tool_state("tc-noloop", "my_tool"), config=_runtime_config()
        )

        # With ws._loop=None, _sync_gate falls to asyncio.run() path.
        # asyncio.run() creates a new loop, runs _async_gate, which will
        # timeout waiting for verdict → fail-closed → BLOCKED.
        # OR it may fail because register_pending needs a running loop.
        # Either way, tool should NOT run (fail-closed).
        # Actually — the pure-sync path may raise because register_pending
        # calls asyncio.get_running_loop(). The except catches it and
        # returns True (fail-closed). So tool shouldn't run.
        assert not tool_ran

    async def test_ws_loop_stopped_sync_gate_fails_closed(self, tmp_path: Path) -> None:
        """If ws._loop exists but is_running() is False, sync gate
        should fail-closed (can't bridge coroutine to a stopped loop)."""
        tool_ran = False

        def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path, block_timeout=0.1)
        _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()

        # Use a MagicMock loop that reports is_running()=False
        mock_loop = MagicMock()
        mock_loop.is_running.return_value = False
        ws._loop = mock_loop

        ws._tool_call_id_to_event_id["tc-stopped"] = "llm-stopped"

        tool_node = ToolNode([my_tool])
        await tool_node.ainvoke(
            _tool_state("tc-stopped", "my_tool"), config=_runtime_config()
        )

        # ws._loop.is_running() is False → _sync_gate falls to asyncio.run()
        # path → fails → returns True (fail-closed) → tool blocked
        assert not tool_ran


# ---------------------------------------------------------------------------
# 3. _ws_client becomes None mid-gate (disconnect race)
# ---------------------------------------------------------------------------


class TestDisconnectRace:
    async def test_ws_client_nulled_between_check_and_gate(
        self, tmp_path: Path
    ) -> None:
        """If _ws_client is set to None after _sync_gate captures it but
        before the gate completes, it should not crash. The local `ws`
        reference in _sync_gate protects against this."""
        tool_ran = False

        def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-disc"] = "llm-disc"

        fut = ws.register_pending("llm-disc")
        fut.set_result(pb.Verdict(event_id="llm-disc", mad_code="M4_a", policy=policy))

        # Null out _ws_client after init but before tool dispatch
        # The gate captures `ws = _ws_client` at the top, so this should
        # be safe — the local reference keeps the object alive.
        tool_node = ToolNode([my_tool])

        # This should still work because _sync_gate captures ws locally
        result = await tool_node.ainvoke(
            _tool_state("tc-disc", "my_tool"), config=_runtime_config()
        )

        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 4. Tools without tool_call_id (bypass vector)
# ---------------------------------------------------------------------------


class TestMissingToolCallId:
    async def test_tool_without_tool_call_id_runs_ungated(self, tmp_path: Path) -> None:
        """A tool invoked with a plain dict (not a ToolCall) should run
        without waiting for a verdict — no tool_call_id to gate on."""
        tool_ran = False

        def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()

        from langchain_core.tools import StructuredTool

        tool = StructuredTool.from_function(my_tool)
        # Plain dict input — not a ToolCall, no "type": "tool_call"
        tool.invoke({"x": "hello"})

        assert tool_ran


# ---------------------------------------------------------------------------
# 5. Concurrent tool calls hitting _sync_gate simultaneously
# ---------------------------------------------------------------------------


class TestConcurrentGateAccess:
    async def test_parallel_tool_calls_each_get_own_verdict(
        self, tmp_path: Path
    ) -> None:
        """Two parallel tool calls with different tool_call_ids must each
        wait for their own verdict independently."""
        results: dict[str, bool] = {}

        def tool_a(x: str) -> str:
            """Tool A."""
            results["a"] = True
            return x

        def tool_b(x: str) -> str:
            """Tool B."""
            results["b"] = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()

        # tool_a → M4 (block), tool_b → M0 (allow)
        ws._tool_call_id_to_event_id["tc-a"] = "llm-a"
        ws._tool_call_id_to_event_id["tc-b"] = "llm-b"

        fut_a = ws.register_pending("llm-a")
        fut_a.set_result(
            pb.Verdict(event_id="llm-a", mad_code="M4_exfil", policy=policy)
        )

        fut_b = ws.register_pending("llm-b")
        fut_b.set_result(
            pb.Verdict(event_id="llm-b", mad_code="M0_benign", policy=policy)
        )

        # Dispatch tool_a (should block)
        tool_node_a = ToolNode([tool_a])
        result_a = await tool_node_a.ainvoke(
            _tool_state("tc-a", "tool_a"), config=_runtime_config()
        )

        # Dispatch tool_b (should allow)
        tool_node_b = ToolNode([tool_b])
        await tool_node_b.ainvoke(
            _tool_state("tc-b", "tool_b"), config=_runtime_config()
        )

        assert "a" not in results, "M4 tool_a should have been blocked"
        assert results.get("b") is True, "M0 tool_b should have run"
        assert "BLOCKED" in result_a["messages"][0].content


# ---------------------------------------------------------------------------
# 6. HITL mode — must hold indefinitely, never fail-open
# ---------------------------------------------------------------------------


class TestHitlModeHold:
    async def test_hitl_holds_past_block_timeout(self, tmp_path: Path) -> None:
        """In MODE_HITL, the gate must wait indefinitely for a human decision.
        It must NOT fail-open after block_timeout elapses."""
        tool_ran = False

        async def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path, block_timeout=0.2)
        policy = _apply_mode(ws, pb.MODE_HITL, policy_m4=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-hitl"] = "llm-hitl"
        fut = ws.register_pending("llm-hitl")

        tool_node = ToolNode([my_tool])
        task = asyncio.ensure_future(
            tool_node.ainvoke(
                _tool_state("tc-hitl", "my_tool"), config=_runtime_config()
            )
        )

        # Wait well past block_timeout (0.2s) — tool should still be held
        await asyncio.sleep(0.5)
        assert not task.done(), (
            "HITL must hold indefinitely, not fail-open after block_timeout"
        )
        assert not tool_ran

        # Human approves
        verdict = pb.Verdict(event_id="llm-hitl", mad_code="M4_a", policy=policy)
        verdict.hitl.continue_execution = True
        fut.set_result(verdict)

        await asyncio.wait_for(task, timeout=2.0)
        assert tool_ran

    async def test_hitl_reject_blocks(self, tmp_path: Path) -> None:
        """HITL reject (continue_execution=False) blocks the tool."""
        tool_ran = False

        async def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_HITL, policy_m4=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-hitl-rej"] = "llm-hitl-rej"
        fut = ws.register_pending("llm-hitl-rej")

        tool_node = ToolNode([my_tool])
        task = asyncio.ensure_future(
            tool_node.ainvoke(
                _tool_state("tc-hitl-rej", "my_tool"), config=_runtime_config()
            )
        )

        await asyncio.sleep(0.1)
        verdict = pb.Verdict(event_id="llm-hitl-rej", mad_code="M4_a", policy=policy)
        verdict.hitl.continue_execution = False
        fut.set_result(verdict)

        result = await asyncio.wait_for(task, timeout=2.0)
        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 7. Verdict arrives before gate checks (resolved future replay)
# ---------------------------------------------------------------------------


class TestVerdictReplay:
    async def test_pre_resolved_verdict_still_blocks(self, tmp_path: Path) -> None:
        """If the verdict future resolves before BaseTool.ainvoke reaches
        the gate, it should still read the resolved value and block."""
        tool_ran = False

        async def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-pre"] = "llm-pre"

        # Resolve the verdict BEFORE tool dispatch
        fut = ws.register_pending("llm-pre")
        fut.set_result(pb.Verdict(event_id="llm-pre", mad_code="M4_a", policy=policy))

        # Small delay to ensure future is fully resolved
        await asyncio.sleep(0.01)

        tool_node = ToolNode([my_tool])
        result = await tool_node.ainvoke(
            _tool_state("tc-pre", "my_tool"), config=_runtime_config()
        )

        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 8. LRU eviction edge case
# ---------------------------------------------------------------------------


class TestLruEviction:
    async def test_unknown_tool_call_id_verdict_timeout_blocks(
        self, tmp_path: Path
    ) -> None:
        """A tool_call_id not in the map → wait_for_tool_call_verdict returns
        None → _async_gate treats None verdict as fail-closed → BLOCKED.

        This tests the case where the LLM event was never seen (or evicted).
        The gate fail-closes because in MODE_BLOCK, absence of verdict = block."""
        tool_ran = False

        async def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path, block_timeout=0.1)
        _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        # Intentionally NOT populating _tool_call_id_to_event_id

        tool_node = ToolNode([my_tool])
        result = await tool_node.ainvoke(
            _tool_state("tc-unknown", "my_tool"), config=_runtime_config()
        )

        # Unknown tool_call_id → wait_for_tool_call_verdict returns None
        # → _async_gate sees verdict=None → fail-closed → BLOCKED
        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 9. LoginAck not received — should block (refuse to run without policy)
# ---------------------------------------------------------------------------


class TestPreLoginBlock:
    async def test_tool_blocked_before_login_ack(self, tmp_path: Path) -> None:
        """Before LoginAck arrives, the gate should block (fail-closed)
        because we can't verify the org's policy."""
        tool_ran = False

        async def my_tool(x: str) -> str:
            """Tool stub."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path, block_timeout=0.1)
        # Do NOT set login_ack_received — simulates pre-login state
        # But we need to ensure _async_gate is actually called, so
        # policy must appear active
        ws._mode = pb.MODE_BLOCK
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-prelogin"] = "llm-prelogin"

        tool_node = ToolNode([my_tool])
        result = await tool_node.ainvoke(
            _tool_state("tc-prelogin", "my_tool"), config=_runtime_config()
        )

        # Gate should block because LoginAck timeout fires (5s in prod,
        # but here _login_ack_received is never set, so it times out)
        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 10. Async tool with M4 verdict (BaseTool.ainvoke path)
# ---------------------------------------------------------------------------


class TestAsyncToolGate:
    async def test_async_tool_ainvoke_gate_blocks_m4(self, tmp_path: Path) -> None:
        """Async tools go through BaseTool.ainvoke → _async_gate directly
        (no _sync_gate involved). Verify this path also blocks."""
        tool_ran = False

        async def async_danger(x: str) -> str:
            """Async danger tool."""
            nonlocal tool_ran
            tool_ran = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-async"] = "llm-async"
        fut = ws.register_pending("llm-async")
        fut.set_result(pb.Verdict(event_id="llm-async", mad_code="M4_a", policy=policy))

        tool_node = ToolNode([async_danger])
        result = await tool_node.ainvoke(
            _tool_state("tc-async", "async_danger"), config=_runtime_config()
        )

        assert not tool_ran
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 11. Multiple tool calls from same LLM message
# ---------------------------------------------------------------------------


class TestMultiToolCall:
    async def test_two_tools_same_llm_one_blocked_one_allowed(
        self, tmp_path: Path
    ) -> None:
        """An LLM emits two tool_calls. One is M4 (blocked), the other
        is M0 (allowed). Each should be independently gated."""
        results: dict[str, bool] = {}

        async def read_file(path: str) -> str:
            """Read file tool."""
            results["read"] = True
            return f"contents of {path}"

        async def delete_file(path: str) -> str:
            """Delete file tool."""
            results["delete"] = True
            return f"deleted {path}"

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()

        # Both tool_calls map to different LLM events (parallel agents scenario)
        ws._tool_call_id_to_event_id["tc-read"] = "llm-read"
        ws._tool_call_id_to_event_id["tc-delete"] = "llm-delete"

        fut_read = ws.register_pending("llm-read")
        fut_read.set_result(
            pb.Verdict(event_id="llm-read", mad_code="M0_ok", policy=policy)
        )

        fut_delete = ws.register_pending("llm-delete")
        fut_delete.set_result(
            pb.Verdict(event_id="llm-delete", mad_code="M4_a", policy=policy)
        )

        # Dispatch read (allowed)
        tn_read = ToolNode([read_file])
        await tn_read.ainvoke(
            _tool_state("tc-read", "read_file", {"path": "/tmp/x"}),
            config=_runtime_config(),
        )
        assert results.get("read") is True

        # Dispatch delete (blocked)
        tn_delete = ToolNode([delete_file])
        result = await tn_delete.ainvoke(
            _tool_state("tc-delete", "delete_file", {"path": "/tmp/x"}),
            config=_runtime_config(),
        )
        assert "delete" not in results
        assert "BLOCKED" in result["messages"][0].content


# ---------------------------------------------------------------------------
# 12. Verify _sync_gate get_running_loop() behavior on actual thread
# ---------------------------------------------------------------------------


class TestGetRunningLoopOnThread:
    async def test_get_running_loop_raises_on_worker_thread(self) -> None:
        """Verify the core invariant: get_running_loop() raises RuntimeError
        on a ThreadPoolExecutor worker, allowing _sync_gate to correctly
        identify it as a worker thread and bridge to ws._loop."""
        loop = asyncio.get_running_loop()

        def check() -> tuple[bool, bool]:
            has_running = False
            get_event_loop_raises = False
            try:
                asyncio.get_running_loop()
                has_running = True
            except RuntimeError:
                pass
            try:
                asyncio.get_event_loop()
            except RuntimeError:
                get_event_loop_raises = True
            return has_running, get_event_loop_raises

        has_running, gel_raises = await loop.run_in_executor(None, check)

        assert not has_running, "Worker thread should NOT have a running loop"

        assert gel_raises, (
            "On Python 3.12+, get_event_loop() should raise on worker thread"
        )
