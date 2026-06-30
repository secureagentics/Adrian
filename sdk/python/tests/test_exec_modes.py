"""End-to-end execution-mode coverage.

Drives the SDK through each of the three server-supplied modes and the
edge cases in the wire protocol, protocol-error, stray HITL resolution,
HITL approve/reject/out-of-scope.  Mode-driven block coverage of the
basic in-scope/out-of-scope/timeout shapes lives in
``tests/test_block_mode.py``; this file focuses on what only HITL +
protocol layers exercise.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import logging
from collections.abc import Iterator
from pathlib import Path  # noqa: TC003
from typing import Any
from unittest.mock import AsyncMock, patch

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
    """Minimal RunnableConfig with a Runtime injected, required by modern ToolNode."""
    return ensure_config({CONF: {CONFIG_KEY_RUNTIME: Runtime()}})


def _hitl_policy() -> pb.PolicySnapshot:
    """A typical HITL policy snapshot with M3 + M4 in scope."""
    return pb.PolicySnapshot(
        mode=pb.MODE_HITL,
        policy_m3=True,
        policy_m4=True,
    )


def _apply_mode(
    ws: WebSocketClient,
    policy: pb.PolicySnapshot,
) -> None:
    """Drive the mode/policy state as if a ``LoginAck`` had arrived."""
    ws._mode = policy.mode
    ws._policy = policy
    ws._login_ack_received.set()


@pytest.fixture(autouse=True)  # pyright: ignore[reportUntypedFunctionDecorator]
def _cleanup() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Ensure each test starts with a clean SDK state."""
    yield
    adrian.shutdown()


def _stub_tool(captured: list[str]) -> Any:  # noqa: ANN401
    async def _impl(x: str) -> str:
        """Stub tool."""
        captured.append(x)

        return x

    return _impl


def _ainvoke_state(tool_call_id: str = "tc-1") -> dict[str, Any]:
    return {
        "messages": [
            AIMessage(
                content="",
                tool_calls=[
                    {"id": tool_call_id, "name": "_impl", "args": {"x": "hi"}},
                ],
            ),
        ],
    }


def _init_with_ws(tmp_path: Path, block_timeout: float = 1.0) -> WebSocketClient:
    """Initialise SDK with a ws_url and return the connected ws stub."""
    adrian.init(
        api_key="k",
        log_file=str(tmp_path / "events.jsonl"),
        auto_instrument=True,
        ws_url="ws://x",
        block_timeout=block_timeout,
    )
    ws = adrian._ws_client
    assert ws is not None
    ws._connected.set()

    return ws


# ------------------------------------------------------------------
# HITL approve / reject / out-of-scope
# ------------------------------------------------------------------


class TestHitlMode:
    async def test_approve_continues(self, tmp_path: Path) -> None:
        """``hitl.continue_execution=True`` → tool runs."""
        captured: list[str] = []
        ws = _init_with_ws(tmp_path)
        _apply_mode(ws, _hitl_policy())
        ws._tool_call_id_to_event_id["tc-1"] = "evt-1"

        verdict = pb.Verdict(event_id="evt-1", mad_code="M4_a", policy=_hitl_policy())
        verdict.hitl.continue_execution = True
        ws.register_pending("evt-1").set_result(verdict)

        result = await ToolNode([_stub_tool(captured)]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
            _ainvoke_state(),
            config=_runtime_config(),
        )

        assert captured == ["hi"]
        assert "BLOCKED" not in result["messages"][0].content

    async def test_reject_halts(self, tmp_path: Path) -> None:
        """``hitl.continue_execution=False`` → halt with synthetic ToolMessage."""
        captured: list[str] = []
        ws = _init_with_ws(tmp_path)
        _apply_mode(ws, _hitl_policy())
        ws._tool_call_id_to_event_id["tc-1"] = "evt-1"

        verdict = pb.Verdict(event_id="evt-1", mad_code="M4_a", policy=_hitl_policy())
        verdict.hitl.continue_execution = False
        ws.register_pending("evt-1").set_result(verdict)

        result = await ToolNode([_stub_tool(captured)]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
            _ainvoke_state(),
            config=_runtime_config(),
        )

        assert captured == []
        assert "BLOCKED" in result["messages"][0].content

    async def test_out_of_scope_continues_without_hitl(
        self,
        tmp_path: Path,
    ) -> None:
        """Out-of-scope verdict is forwarded immediately without ``hitl``;

        the SDK treats it like any out-of-scope verdict and continues.
        """
        captured: list[str] = []
        ws = _init_with_ws(tmp_path)
        _apply_mode(ws, _hitl_policy())  # m2 stays False
        ws._tool_call_id_to_event_id["tc-1"] = "evt-1"

        # No hitl field → server forwarded the M2 verdict immediately
        # because policy_m2=false made it out of scope for HITL review.
        verdict = pb.Verdict(event_id="evt-1", mad_code="M2", policy=_hitl_policy())
        ws.register_pending("evt-1").set_result(verdict)

        await ToolNode([_stub_tool(captured)]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
            _ainvoke_state(),
            config=_runtime_config(),
        )

        assert captured == ["hi"]

    async def test_error_review_approve_continues(self, tmp_path: Path) -> None:
        """ERROR verdict + HITL approve still resumes the tool."""
        captured: list[str] = []
        ws = _init_with_ws(tmp_path)
        policy = pb.PolicySnapshot(
            mode=pb.MODE_HITL,
            fail_closed_on_classifier_error=True,
        )
        _apply_mode(ws, policy)
        ws._tool_call_id_to_event_id["tc-1"] = "evt-1"

        verdict = pb.Verdict(
            event_id="evt-1",
            status=pb.VERDICT_STATUS_ERROR,
            mad_code="",
            policy=policy,
        )
        verdict.hitl.continue_execution = True
        ws.register_pending("evt-1").set_result(verdict)

        result = await ToolNode([_stub_tool(captured)]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
            _ainvoke_state(),
            config=_runtime_config(),
        )

        assert captured == ["hi"]
        assert "BLOCKED" not in result["messages"][0].content

    async def test_error_review_reject_halts(self, tmp_path: Path) -> None:
        """ERROR verdict + HITL reject blocks the tool."""
        captured: list[str] = []
        ws = _init_with_ws(tmp_path)
        policy = pb.PolicySnapshot(
            mode=pb.MODE_HITL,
            fail_closed_on_classifier_error=True,
        )
        _apply_mode(ws, policy)
        ws._tool_call_id_to_event_id["tc-1"] = "evt-1"

        verdict = pb.Verdict(
            event_id="evt-1",
            status=pb.VERDICT_STATUS_ERROR,
            mad_code="",
            policy=policy,
        )
        verdict.hitl.continue_execution = False
        ws.register_pending("evt-1").set_result(verdict)

        result = await ToolNode([_stub_tool(captured)]).ainvoke(  # pyright: ignore[reportUnknownMemberType]
            _ainvoke_state(),
            config=_runtime_config(),
        )

        assert captured == []
        assert "BLOCKED" in result["messages"][0].content


# ------------------------------------------------------------------
# Stray HITL resolution + protocol error
# ------------------------------------------------------------------


class _FakeWs:
    """Minimal async-iterable stand-in for a real websocket connection."""

    def __init__(self, messages: list[bytes]) -> None:
        self._messages = messages

    def __aiter__(self) -> _FakeWs:
        return self

    async def __anext__(self) -> bytes:
        if not self._messages:
            raise StopAsyncIteration
        return self._messages.pop(0)


class TestRecvLoopProtocol:
    async def test_stray_hitl_resolution_logs_and_drops(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """A HITL verdict for an unknown event_id is dropped with a WARN.

        Defensive against the SDK process restarting between event send
        and HITL resolution: the dashboard's resolution arrives on a
        connection that no longer has the original pending future.
        """
        login_ack = pb.ServerFrame()
        login_ack.login_ack.policy.mode = pb.MODE_HITL
        login_ack.login_ack.policy.policy_m4 = True

        verdict_frame = pb.ServerFrame()
        verdict_frame.verdict.event_id = "stale-evt"
        verdict_frame.verdict.session_id = "sess-1"
        verdict_frame.verdict.mad_code = "M4_a"
        verdict_frame.verdict.hitl.continue_execution = True

        client = WebSocketClient("ws://x", "s", api_key="k")
        client._ws = _FakeWs(  # type: ignore[assignment]
            [login_ack.SerializeToString(), verdict_frame.SerializeToString()],
        )

        with caplog.at_level(logging.WARNING, logger="adrian.ws"):
            await client._recv_loop()

        msgs = [r.getMessage() for r in caplog.records]
        assert any("stale-evt" in m and "stale" in m.lower() for m in msgs)
        assert client._mode == pb.MODE_HITL  # LoginAck was applied

    async def test_protocol_error_first_frame_not_login_ack(
        self,
        caplog: pytest.LogCaptureFixture,
    ) -> None:
        """First frame must be ``login_ack``; anything else exits the loop."""
        verdict_frame = pb.ServerFrame()
        verdict_frame.verdict.event_id = "evt-1"

        client = WebSocketClient("ws://x", "s", api_key="k")
        client._ws = _FakeWs([verdict_frame.SerializeToString()])  # type: ignore[assignment]

        # _handle_disconnect's reconnect spawn would race; suppress the
        # connect path so the test can assert on the error log alone.
        with (
            caplog.at_level(logging.ERROR, logger="adrian.ws"),
            patch.object(client, "connect", AsyncMock()),
        ):
            await client._recv_loop()

        msgs = [r.getMessage() for r in caplog.records]
        assert any("expected ServerFrame{login_ack}" in m for m in msgs)
