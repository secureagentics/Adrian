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
    fail_closed_on_classifier_error: bool = False,
) -> pb.PolicySnapshot:
    """Drive the mode/policy state as if a LoginAck had arrived."""
    policy = pb.PolicySnapshot(
        mode=cast("pb.Mode", mode),
        policy_m0=policy_m0,
        policy_m2=policy_m2,
        policy_m3=policy_m3,
        policy_m4=policy_m4,
        fail_closed_on_classifier_error=fail_closed_on_classifier_error,
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
        """MODE_BLOCK + policy_m4=true + mad_code='M4_a' → halt with synthetic ToolMessage."""

        def _real_tool(x: str) -> str:
            """Real tool stub for block-mode tests."""
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

        assert _real_tool.called is False  # type: ignore[attr-defined]
        msgs = result["messages"]
        assert len(msgs) == 1
        assert "BLOCKED" in msgs[0].content

    async def test_out_of_scope_verdict_runs_tool(self, tmp_path: Path) -> None:
        """MODE_BLOCK with policy_m2=false + mad_code='M2' → continue (out-of-scope)."""

        captured: list[str] = []

        def _real_tool(x: str) -> str:
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

    async def test_timeout_fail_open_runs_tool(self, tmp_path: Path) -> None:
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Real tool stub for block-mode tests."""
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
        # No pending future → wait_for_verdict times out → fail-open.

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == ["hi"]

    async def test_timeout_fail_closed_blocks_tool(self, tmp_path: Path) -> None:
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Real tool stub for block-mode tests."""
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
        _apply_mode(
            ws,
            pb.MODE_BLOCK,
            policy_m4=True,
            fail_closed_on_classifier_error=True,
        )
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        result = await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == []
        assert "BLOCKED" in result["messages"][0].content

    async def test_error_verdict_fail_open_runs_tool(self, tmp_path: Path) -> None:
        captured: list[str] = []

        def _real_tool(x: str) -> str:
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
        policy = _apply_mode(ws, pb.MODE_BLOCK, fail_closed_on_classifier_error=False)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"

        fut = ws.register_pending("llm-evt")
        fut.set_result(
            pb.Verdict(
                event_id="llm-evt",
                status=pb.VERDICT_STATUS_ERROR,
                mad_code="",
                policy=policy,
            ),
        )

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == ["hi"]

    async def test_error_verdict_fail_closed_blocks_tool(
        self,
        tmp_path: Path,
    ) -> None:
        captured: list[str] = []

        def _real_tool(x: str) -> str:
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
        policy = _apply_mode(ws, pb.MODE_BLOCK, fail_closed_on_classifier_error=True)
        ws._connected.set()
        ws._tool_call_id_to_event_id["tc-1"] = "llm-evt"

        fut = ws.register_pending("llm-evt")
        fut.set_result(
            pb.Verdict(
                event_id="llm-evt",
                status=pb.VERDICT_STATUS_ERROR,
                mad_code="",
                policy=policy,
            ),
        )

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        result = await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == []
        assert "BLOCKED" in result["messages"][0].content

    async def test_unknown_tool_call_stays_fail_open_when_fail_closed(
        self,
        tmp_path: Path,
    ) -> None:
        captured: list[str] = []

        def _real_tool(x: str) -> str:
            """Real tool stub for block-mode tests."""
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
        _apply_mode(ws, pb.MODE_BLOCK, fail_closed_on_classifier_error=True)
        ws._connected.set()

        tool_node = ToolNode([_real_tool])
        ai = AIMessage(
            content="",
            tool_calls=[{"id": "tc-1", "name": "_real_tool", "args": {"x": "hi"}}],
        )
        state: dict[str, Any] = {"messages": [ai]}

        await tool_node.ainvoke(state, config=_runtime_config())  # pyright: ignore[reportUnknownMemberType]

        assert captured == ["hi"]


class TestModeAlert:
    async def test_alert_mode_skips_wait(self, tmp_path: Path) -> None:
        """MODE_ALERT: ``policy_active()`` returns False → tool runs without registering a future."""

        captured: list[str] = []

        def _real_tool(x: str) -> str:
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
