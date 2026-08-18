"""Regression test for run 019eda10-b934-7d50-a16f-5aca881c5ee9.

Bug: In Python 3.12+, ``asyncio.get_event_loop()`` raises RuntimeError on
bare worker threads (no loop set). The old ``_sync_gate`` caught that
RuntimeError and returned False (skip gate), so M4 tool calls executed
unblocked when dispatched from Pregel's ThreadPoolExecutor workers.

Fix: ``_sync_gate`` now uses ``get_running_loop()`` (which correctly reports
"no running loop on THIS thread" on a worker thread) and falls through to
the ``run_coroutine_threadsafe`` bridge onto the WS client's loop.

This test replicates the exact production scenario: a sync tool dispatched
by ToolNode.ainvoke (which uses run_in_executor internally), with a
MODE_BLOCK + policy_m4=True + M4 verdict. The tool body must NOT execute.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import Any, cast
from unittest.mock import patch

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


class TestSyncGateWorkerThreadRegression:
    """Reproduce run 019eda10: M4 verdict present but sync tool ran anyway.

    The root cause was ``_sync_gate`` using ``asyncio.get_event_loop()``
    which raises RuntimeError on Python 3.12+ worker threads, causing the
    gate to skip entirely and return False.
    """

    async def test_sync_tool_on_worker_thread_blocks_m4(self, tmp_path: Path) -> None:
        """Sync tool dispatched from a ThreadPoolExecutor worker (like Pregel)
        must be blocked when an M4 verdict is in scope."""
        tool_executed = False

        def dangerous_tool(x: str) -> str:
            """Simulates a dangerous tool that should be blocked."""
            nonlocal tool_executed
            tool_executed = True
            return f"EXECUTED: {x}"

        adrian.init(
            api_key="test-key",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=2.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-m4"] = "llm-evt-m4"

        fut = ws.register_pending("llm-evt-m4")
        fut.set_result(
            pb.Verdict(event_id="llm-evt-m4", mad_code="M4_exfiltration", policy=policy)
        )

        tool_node = ToolNode([dangerous_tool])
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-m4", "name": "dangerous_tool", "args": {"x": "steal data"}}
            ],
        )
        state: dict[str, Any] = {"messages": [ai]}

        result = await tool_node.ainvoke(state, config=_runtime_config())

        assert not tool_executed, (
            "CRITICAL: M4-flagged tool executed despite block verdict! "
            "This is the run 019eda10 regression."
        )
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "BLOCKED" in msgs[0].content

    async def test_sync_gate_does_not_use_get_event_loop(self, tmp_path: Path) -> None:
        """Verify _sync_gate never calls asyncio.get_event_loop().

        The old buggy code used get_event_loop() which raises RuntimeError
        on Python 3.12+ worker threads and caused the gate to skip.
        """
        adrian.init(
            api_key="test-key",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=2.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"

        fut = ws.register_pending("llm-evt")
        fut.set_result(pb.Verdict(event_id="llm-evt", mad_code="M4_a", policy=policy))

        original_get_event_loop = asyncio.get_event_loop
        get_event_loop_called_from_worker = False

        def tracking_get_event_loop():
            nonlocal get_event_loop_called_from_worker
            try:
                asyncio.get_running_loop()
            except RuntimeError:
                # We're on a worker thread — this is the buggy call path
                get_event_loop_called_from_worker = True
            return original_get_event_loop()

        def passthrough_tool(x: str) -> str:
            """Passthrough."""
            return x

        with patch("asyncio.get_event_loop", side_effect=tracking_get_event_loop):
            tool_node = ToolNode([passthrough_tool])
            ai = AIMessage(
                content="",
                tool_calls=[
                    {"id": "tc-1", "name": "passthrough_tool", "args": {"x": "hi"}}
                ],
            )
            state: dict[str, Any] = {"messages": [ai]}
            await tool_node.ainvoke(state, config=_runtime_config())

        assert not get_event_loop_called_from_worker, (
            "BUG: _sync_gate called asyncio.get_event_loop() from a worker thread. "
            "This is the root cause of the run 019eda10 bypass."
        )

    async def test_m0_benign_tool_still_runs(self, tmp_path: Path) -> None:
        """M0 (benign) verdict with policy_m0=False should let the tool run.

        Ensures the gate doesn't over-block — only in-scope verdicts halt.
        """
        captured: list[str] = []

        def safe_tool(x: str) -> str:
            """Safe tool stub."""
            captured.append(x)
            return x

        adrian.init(
            api_key="test-key",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=2.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)  # m0 not in scope
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-benign"] = "llm-evt-benign"

        fut = ws.register_pending("llm-evt-benign")
        fut.set_result(
            pb.Verdict(event_id="llm-evt-benign", mad_code="M0_benign", policy=policy)
        )

        tool_node = ToolNode([safe_tool])
        ai = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-benign", "name": "safe_tool", "args": {"x": "hello"}}
            ],
        )
        state: dict[str, Any] = {"messages": [ai]}
        await tool_node.ainvoke(state, config=_runtime_config())

        assert captured == ["hello"], "Benign M0 tool should have executed"


class TestOldBuggyGateWouldFail:
    """Prove the old buggy _sync_gate pattern would fail on this Python version.

    The old code did:
        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return False  # <-- BUG: skips gate entirely

    On Python 3.12+ worker threads, get_event_loop() raises RuntimeError
    because there's no current event loop set for that thread.
    """

    async def test_get_event_loop_raises_on_worker_thread(self) -> None:
        """Confirm the failure mode exists on this Python version."""
        loop = asyncio.get_running_loop()

        def check_on_worker() -> str:
            try:
                asyncio.get_running_loop()
                return "has_running_loop"
            except RuntimeError:
                pass

            try:
                asyncio.get_event_loop()
                return "get_event_loop_succeeded"
            except RuntimeError:
                return "get_event_loop_raised"

        result = await loop.run_in_executor(None, check_on_worker)

        # On Python 3.12+, this should be "get_event_loop_raised"
        # On Python 3.10-3.11, it may succeed (creating a new loop)
        # Either way, the fix handles both: it uses get_running_loop() instead
        import sys

        assert result == "get_event_loop_raised", (
            f"Expected get_event_loop() to raise on worker thread "
            f"(Python {sys.version_info}), got: {result}"
        )
        # On older Python, get_event_loop may succeed but the old code still
        # had the wrong behavior (would try run_until_complete on a non-running
        # loop that wasn't connected to ws._loop)
