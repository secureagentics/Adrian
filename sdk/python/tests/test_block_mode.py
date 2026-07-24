# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Tests for the patched ``ToolNode`` halt path in ``MODE_BLOCK``."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path  # noqa: TC003
from typing import Any, cast
from unittest.mock import AsyncMock, patch

import adrian
import pytest
from adrian.format.types import AgentContext, LlmPairData, PairedEvent, ToolPairData
from adrian.proto import event_pb2 as pb
from adrian.ws import WebSocketClient
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import RunnableConfig, ensure_config
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime


def _runtime_config() -> RunnableConfig:
    """Minimal RunnableConfig with a Runtime injected, required by modern ToolNode."""
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
    """Drive the mode/policy state as if a LoginAck had arrived."""
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


@pytest.fixture(autouse=True)  # pyright: ignore[reportUntypedFunctionDecorator]
def _cleanup() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Ensure each test starts with a clean SDK state."""
    yield
    adrian.shutdown()


def _llm_pair_with_tool_call(
    event_id: str = "llm-evt",
    run_id: str = "llm-run",
    tool_call_id: str = "tc-1",
) -> PairedEvent:
    return PairedEvent(
        event_id=event_id,
        invocation_id="inv",
        session_id="sess",
        run_id=run_id,
        timestamp="2026-01-01T00:00:00Z",
        pair_type="llm",
        agent=AgentContext(agent_id="a"),
        parent=None,
        data=LlmPairData(
            model="ChatAnthropic",
            output="I will call a tool",
            tool_calls=[{"id": tool_call_id, "name": "t", "args": {}}],  # type: ignore[list-item]
        ),
    )


def _tool_pair() -> PairedEvent:
    return PairedEvent(
        event_id="tool-evt",
        invocation_id="inv",
        session_id="sess",
        run_id="tool-run",
        parent_run_id="llm-run",
        timestamp="2026-01-01T00:00:00Z",
        pair_type="tool",
        agent=AgentContext(agent_id="a"),
        parent=None,
        data=ToolPairData(tool_name="t", input="{}", output="ok"),
    )


class TestRunIdCorrelation:
    async def test_llm_pair_populates_run_id_map(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(_llm_pair_with_tool_call())

        assert client._run_id_to_event_id["llm-run"] == "llm-evt"

    async def test_tool_pair_does_not_touch_run_id_map(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(_tool_pair())

        assert "tool-run" not in client._run_id_to_event_id


class TestWaitForToolVerdict:
    async def test_fail_open_when_no_llm_context(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        result = await client.wait_for_tool_verdict("unseen", timeout=0.01)
        assert result is None

    async def test_looks_up_llm_event_id_and_times_out(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        client._run_id_to_event_id["llm-run"] = "llm-evt"
        result = await client.wait_for_tool_verdict("llm-run", timeout=0.05)
        assert result is None

    async def test_looks_up_llm_event_id_and_resolves(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        client._run_id_to_event_id["llm-run"] = "llm-evt"
        fut = client.register_pending("llm-evt")
        fut.set_result(pb.Verdict(event_id="llm-evt"))

        result = await client.wait_for_tool_verdict("llm-run", timeout=1.0)
        assert result is not None
        assert result.event_id == "llm-evt"


class TestToolNodePatchBlocking:
    async def test_in_scope_block_verdict_halts_tool(self, tmp_path: Path) -> None:
        """MODE_BLOCK + policy_m4=true + mad_code='M4_a' → BaseTool.ainvoke gate blocks.

        The verdict gate lives on BaseTool (the universal layer), not
        ToolNode.ainvoke. Uses an async tool so BaseTool.ainvoke (not
        BaseTool.invoke) is the entry point - matching the production
        path for create_react_agent with async tools.
        """

        async def _real_tool(x: str) -> str:
            """Real async tool stub for block-mode tests."""
            _real_tool.called = True  # type: ignore[attr-defined]

            return x

        _real_tool.called = False  # type: ignore[attr-defined]

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=1.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"

        fut = ws.register_pending("llm-evt")
        fut.set_result(
            pb.Verdict(event_id="llm-evt", mad_code="M4_a", policy=policy),
        )

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        result = await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        # BaseTool.ainvoke gate blocks - tool body does NOT run.
        assert _real_tool.called is False  # type: ignore[attr-defined]
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "BLOCKED" in msgs[0].content

    async def test_out_of_scope_verdict_runs_tool(self, tmp_path: Path) -> None:
        """MODE_BLOCK with policy_m2=false + mad_code='M2' → continue (out-of-scope)."""

        captured: list[str] = []

        async def _real_tool(x: str) -> str:
            """Real tool stub for block-mode tests."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=1.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)  # m2 stays False
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"

        fut = ws.register_pending("llm-evt")
        fut.set_result(
            pb.Verdict(event_id="llm-evt", mad_code="M2", policy=policy),
        )

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == ["hi"]

    async def test_timeout_fail_closed_blocks_tool(self, tmp_path: Path) -> None:
        """Verdict timeout in MODE_BLOCK → fail-closed (tool does NOT run)."""
        captured: list[str] = []

        async def _real_tool(x: str) -> str:
            """Real async tool stub for block-mode tests."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=0.05,
        )

        ws = adrian._ws_client
        assert ws is not None
        _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"
        # No pending future → wait_for_verdict times out → fail-closed (MODE_BLOCK).

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        # Fail-closed: tool should NOT have run.
        assert captured == []


class TestModeAlert:
    async def test_alert_mode_skips_wait(self, tmp_path: Path) -> None:
        """MODE_ALERT: ``policy_active()`` returns False → tool runs without registering a future."""

        captured: list[str] = []

        async def _real_tool(x: str) -> str:
            """Real tool stub for block-mode tests."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
        )

        ws = adrian._ws_client
        assert ws is not None
        _apply_mode(ws, pb.MODE_ALERT)

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await asyncio.wait_for(
            tool_node.ainvoke(state, config=_runtime_config()),  # pyright: ignore[reportUnknownMemberType]
            timeout=1.0,
        )

        assert captured == ["hi"]
        assert not ws._pending_verdicts


class TestSyncToolNodeBlocking:
    """Regression: sync (``def``) tools dispatched by ToolNode / create_react_agent.

    The tests in ``TestToolNodePatchBlocking`` use ``async def`` tools, so
    they exercise ``BaseTool.ainvoke`` (the async gate). A sync ``def`` tool
    takes a different path: ``StructuredTool.ainvoke`` has no coroutine, so it
    runs ``self.invoke`` via ``run_in_executor`` on a worker thread. The gate
    therefore lands in ``BaseTool.invoke`` -> ``_sync_gate`` on a thread that
    is not running an event loop, and ``_sync_gate`` must bridge the gate onto
    the WS loop. A regression here (e.g. probing the thread with
    ``get_event_loop()``, which raises on a worker thread) silently skips the
    gate and lets block-level tool calls run ungated under create_react_agent.
    """

    @staticmethod
    def _prep(ws: WebSocketClient, policy_m4: bool, mad_code: str) -> None:
        """Drive a logged-in MODE_BLOCK state with a pre-resolved verdict.

        ``ws._loop`` points at the test loop so the worker-thread bridge in
        ``_sync_gate`` has a running target, mirroring production where the
        WS loop lives on its own thread, separate from the Pregel worker.
        """
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=policy_m4)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"
        fut = ws.register_pending("llm-evt")
        fut.set_result(pb.Verdict(event_id="llm-evt", mad_code=mad_code, policy=policy))

    async def test_sync_tool_block_verdict_halts(self, tmp_path: Path) -> None:
        """MODE_BLOCK + policy_m4 + M4 verdict: sync tool body must NOT run."""
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Sync tool stub; records execution."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=1.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        self._prep(ws, policy_m4=True, mad_code="M4_a")

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        result = await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        # Sync tool body must NOT run; a BLOCKED ToolMessage is returned.
        assert captured == []
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "BLOCKED" in msgs[0].content

    async def test_sync_tool_out_of_scope_runs(self, tmp_path: Path) -> None:
        """MODE_BLOCK, M2 verdict with policy_m2 false: sync tool runs (no over-block)."""
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Sync tool stub; records execution."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=1.0,
        )

        ws = adrian._ws_client
        assert ws is not None
        self._prep(ws, policy_m4=True, mad_code="M2")  # m2 not in policy scope

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == ["hi"]

    @staticmethod
    def _prep_hitl(
        ws: WebSocketClient,
    ) -> tuple[pb.PolicySnapshot, asyncio.Future[pb.Verdict]]:
        """MODE_HITL, logged in, with an UNRESOLVED pending verdict (held).

        Returns the policy and the pending future so the test can resolve it
        later, standing in for a human approve/reject.
        """
        policy = _apply_mode(ws, pb.MODE_HITL, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"
        fut = ws.register_pending("llm-evt")
        return policy, fut

    @staticmethod
    def _tool_call_state() -> dict[str, Any]:
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        return {"messages": [ai]}

    async def test_sync_tool_hitl_holds_until_human_then_blocks_on_reject(
        self, tmp_path: Path
    ) -> None:
        """MODE_HITL: a sync tool is HELD indefinitely, never fail-opens.

        The gate must wait past ``block_timeout`` (the bounded MODE_BLOCK wait
        does not apply to HITL); a human reject then halts the tool. Regression
        for the worker-thread bridge fail-opening a HITL hold once a finite
        ``future.result`` timeout elapsed.
        """
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Sync tool stub; records execution."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=0.5,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy, fut = self._prep_hitl(ws)

        task = asyncio.ensure_future(
            ToolNode([_real_tool]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
                self._tool_call_state(), config=_runtime_config()
            )
        )

        # Held well past block_timeout: neither run nor returned, waiting for a human.
        await asyncio.sleep(1.5)
        assert not task.done()
        assert captured == []

        # Human rejects -> HITL verdict with continue_execution=False.
        verdict = pb.Verdict(event_id="llm-evt", mad_code="M4_a", policy=policy)
        verdict.hitl.continue_execution = False
        fut.set_result(verdict)

        result = await asyncio.wait_for(task, timeout=2.0)
        assert captured == []
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "BLOCKED" in msgs[0].content

    async def test_sync_tool_hitl_resumes_on_approve(self, tmp_path: Path) -> None:
        """MODE_HITL: after a human approve (continue_execution=True), the sync tool runs."""
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Sync tool stub; records execution."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=0.5,
        )

        ws = adrian._ws_client
        assert ws is not None
        policy, fut = self._prep_hitl(ws)

        task = asyncio.ensure_future(
            ToolNode([_real_tool]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
                self._tool_call_state(), config=_runtime_config()
            )
        )

        await asyncio.sleep(0.3)
        assert not task.done()
        assert captured == []

        verdict = pb.Verdict(event_id="llm-evt", mad_code="M4_a", policy=policy)
        verdict.hitl.continue_execution = True
        fut.set_result(verdict)

        await asyncio.wait_for(task, timeout=2.0)
        assert captured == ["hi"]

    async def test_sync_tool_block_timeout_fails_closed(self, tmp_path: Path) -> None:
        """MODE_BLOCK: no verdict before block_timeout -> sync tool blocked (fail-closed)."""
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Sync tool stub; records execution."""
            captured.append(x)

            return x

        adrian.init(
            api_key="k",
            log_file=str(tmp_path / "events.jsonl"),
            auto_instrument=True,
            ws_url="ws://x",
            block_timeout=0.1,
        )

        ws = adrian._ws_client
        assert ws is not None
        _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"
        ws.register_pending("llm-evt")  # never resolved -> verdict times out

        result = await ToolNode([_real_tool]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
            self._tool_call_state(), config=_runtime_config()
        )

        assert captured == []
        assert "BLOCKED" in result["messages"][0].content
