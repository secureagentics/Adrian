"""Tests for the WebSocket client, new PairedEvent format."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
import contextlib
import json
from unittest.mock import AsyncMock, patch

from adrian.format.types import (
    AgentContext,
    LlmPairData,
    PairedEvent,
    ParentContext,
    ToolPairData,
)
from adrian.proto import event_pb2 as pb
from adrian.ws import (
    SCHEMA_VERSION,
    WebSocketClient,
    _derive_provider,
    _paired_event_to_proto,
)


def _tool_pair(event_id: str = "e1", run_id: str = "r1") -> PairedEvent:
    return PairedEvent(
        event_id=event_id,
        invocation_id="inv",
        session_id="sess-1",
        run_id=run_id,
        timestamp="2026-01-01T00:00:00Z",
        pair_type="tool",
        agent=AgentContext(agent_id="a"),
        parent=None,
        data=ToolPairData(tool_name="t", input="{}", output="ok"),
    )


def _llm_pair(
    event_id: str = "e1",
    run_id: str = "r1",
    tool_calls: list[dict[str, object]] | None = None,
) -> PairedEvent:
    return PairedEvent(
        event_id=event_id,
        invocation_id="inv",
        session_id="sess-1",
        run_id=run_id,
        timestamp="2026-01-01T00:00:00Z",
        pair_type="llm",
        agent=AgentContext(agent_id="a", system_prompt="be helpful"),
        parent=None,
        data=LlmPairData(
            model="ChatAnthropic",
            messages=[{"role": "system", "content": "be helpful"}],  # type: ignore[list-item]
            output="sure",
            tool_calls=tool_calls or [],  # type: ignore[arg-type]
        ),
    )


# ------------------------------------------------------------------
# Conversion
# ------------------------------------------------------------------


class TestPairedEventToProto:
    def test_tool_pair_envelope(self) -> None:
        pe = _tool_pair()
        proto = _paired_event_to_proto(pe)
        assert proto.event_id == "e1"
        assert proto.pair_type == pb.PAIR_TYPE_TOOL
        assert proto.session_id == "sess-1"
        assert proto.agent.agent_id == "a"
        # No parent → parent.agent_id empty string sentinel
        assert proto.parent.agent_id == ""

    def test_tool_pair_payload(self) -> None:
        pe = _tool_pair()
        proto = _paired_event_to_proto(pe)
        assert proto.tool.tool_name == "t"
        assert proto.tool.input == "{}"
        assert proto.tool.output == "ok"

    def test_llm_pair_with_tool_calls(self) -> None:
        pe = _llm_pair(
            tool_calls=[{"id": "tc-1", "name": "foo", "args": {"x": 1}}],
        )
        proto = _paired_event_to_proto(pe)
        assert proto.pair_type == pb.PAIR_TYPE_LLM
        assert proto.llm.model == "ChatAnthropic"
        assert len(proto.llm.messages) == 1
        assert proto.llm.messages[0].role == "system"
        assert len(proto.llm.tool_calls) == 1
        assert proto.llm.tool_calls[0].id == "tc-1"
        assert proto.llm.tool_calls[0].name == "foo"

    def test_parent_context(self) -> None:
        pe = _tool_pair()
        pe.parent = ParentContext(
            agent_id="parent-a",
            system_prompt="parent sys",
            user_instruction="parent ui",
        )
        proto = _paired_event_to_proto(pe)
        assert proto.parent.agent_id == "parent-a"
        assert proto.parent.system_prompt == "parent sys"
        assert proto.parent.user_instruction == "parent ui"

    def test_parent_run_id_propagates(self) -> None:
        pe = _tool_pair()
        pe.parent_run_id = "parent-r"
        proto = _paired_event_to_proto(pe)
        assert proto.parent_run_id == "parent-r"

    def test_metadata_json(self) -> None:
        pe = _tool_pair()
        pe.metadata = {"langgraph_node": "tools"}
        proto = _paired_event_to_proto(pe)

        decoded = json.loads(proto.metadata_json.decode())
        assert decoded["langgraph_node"] == "tools"


# ------------------------------------------------------------------
# Provider detection
# ------------------------------------------------------------------


class TestDeriveProvider:
    def test_anthropic(self) -> None:
        assert _derive_provider("ChatAnthropic") == "anthropic"

    def test_openai(self) -> None:
        assert _derive_provider("ChatOpenAI") == "openai"

    def test_unknown_falls_back_to_lowercase(self) -> None:
        assert _derive_provider("CustomLLM") == "customllm"


# ------------------------------------------------------------------
# on_paired_event (EventHandler protocol)
# ------------------------------------------------------------------


class TestOnPairedEvent:
    async def test_connect_then_send_ships_login_and_event(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "sess-1", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(_llm_pair())

        assert mock_ws.send.await_count == 2  # login + paired_batch
        first = pb.ClientFrame()
        first.ParseFromString(mock_ws.send.await_args_list[0][0][0])
        assert first.WhichOneof("frame") == "login"
        assert first.login.schema_version == SCHEMA_VERSION

        second = pb.ClientFrame()
        second.ParseFromString(mock_ws.send.await_args_list[1][0][0])
        assert second.WhichOneof("frame") == "paired_batch"

    async def test_reuses_connection(self) -> None:
        mock_ws = AsyncMock()
        mock_connect = AsyncMock(return_value=mock_ws)
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", mock_connect):
            await client.connect()
            await client.on_paired_event(_tool_pair("e1", "r1"))
            await client.on_paired_event(_tool_pair("e2", "r2"))

        mock_connect.assert_awaited_once()
        assert mock_ws.send.await_count == 3  # login + 2 events

    async def test_auto_detect_llm_stack_on_first_llm_pair(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(_llm_pair())

        assert client._provider == "anthropic"
        assert client._model == "ChatAnthropic"


# ------------------------------------------------------------------
# Login frame contents
# ------------------------------------------------------------------


class TestLoginFrame:
    async def test_login_contains_session_and_schema(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "sess-42", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(_llm_pair())

        first = pb.ClientFrame()
        first.ParseFromString(mock_ws.send.await_args_list[0][0][0])
        assert first.login.session_id == "sess-42"
        assert first.login.schema_version == SCHEMA_VERSION
        assert first.login.llm_stack.provider == "anthropic"


# ------------------------------------------------------------------
# Connect
# ------------------------------------------------------------------


class TestConnect:
    async def test_connect_succeeds(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        assert client._connected.is_set()
        assert client._ws is mock_ws

    async def test_connect_retries_on_failure(self) -> None:
        mock_ws = AsyncMock()
        mock_connect = AsyncMock(
            side_effect=[ConnectionError("refused"), mock_ws],
        )
        client = WebSocketClient("ws://x", "s", api_key="k")

        with (
            patch("adrian.ws.websockets.connect", mock_connect),
            patch("adrian.ws.asyncio.sleep", new_callable=AsyncMock),
        ):
            await client.connect()

        assert mock_connect.await_count == 2
        assert client._connected.is_set()

    async def test_connect_spawns_recv_task(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        assert client._recv_task is not None
        assert isinstance(client._recv_task, asyncio.Task)

        client._recv_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await client._recv_task


# ------------------------------------------------------------------
# Verdict receive loop
# ------------------------------------------------------------------


class TestRecvLoop:
    async def test_recv_loop_resolves_pending_future(self) -> None:
        login_ack = pb.ServerFrame()
        login_ack.login_ack.policy.mode = pb.MODE_BLOCK
        login_ack.login_ack.policy.policy_m4 = True

        verdict_frame = pb.ServerFrame()
        verdict_frame.verdict.event_id = "evt-1"
        verdict_frame.verdict.session_id = "sess-1"
        verdict_frame.verdict.mad_code = "M4_a"

        class _FakeWs:
            def __init__(self, messages: list[bytes]) -> None:
                self._messages = messages

            def __aiter__(self) -> _FakeWs:
                return self

            async def __anext__(self) -> bytes:
                if not self._messages:
                    raise StopAsyncIteration
                return self._messages.pop(0)

        client = WebSocketClient("ws://x", "s", api_key="k")
        client._ws = _FakeWs(  # type: ignore[assignment]
            [login_ack.SerializeToString(), verdict_frame.SerializeToString()],
        )
        fut = client.register_pending("evt-1")

        await client._recv_loop()

        assert fut.done()
        resolved = fut.result()
        assert resolved.event_id == "evt-1"
        assert resolved.mad_code == "M4_a"


# ------------------------------------------------------------------
# Block-mode primitives
# ------------------------------------------------------------------


class TestBlockModePrimitives:
    async def test_wait_for_verdict_timeout(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        result = await client.wait_for_verdict("missing", timeout=0.01)
        assert result is None

    async def test_wait_for_tool_verdict_without_llm_context_fails_open(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        result = await client.wait_for_tool_verdict("unseen-run", timeout=0.01)
        # No LLM context for that run_id → fail-open (return None), no wait.
        assert result is None

    async def test_on_paired_event_populates_run_id_map(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(
                _llm_pair(event_id="llm-evt", run_id="llm-run"),
            )

        assert client._run_id_to_event_id["llm-run"] == "llm-evt"

    async def test_on_paired_event_populates_tool_call_id_map(self) -> None:
        """Each tool_call.id the LLM emitted maps to the LLM pair's event_id."""
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(
                _llm_pair(
                    event_id="llm-evt",
                    tool_calls=[
                        {"id": "tc-a", "name": "foo", "args": {}},
                        {"id": "tc-b", "name": "bar", "args": {}},
                    ],
                ),
            )

        assert client._tool_call_id_to_event_id["tc-a"] == "llm-evt"
        assert client._tool_call_id_to_event_id["tc-b"] == "llm-evt"

    async def test_empty_tool_call_id_is_skipped(self) -> None:
        """An empty tool_call.id must not land in the map (fail-open path)."""
        mock_ws = AsyncMock()
        client = WebSocketClient("ws://x", "s", api_key="k")

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(
                _llm_pair(
                    event_id="llm-evt",
                    tool_calls=[{"id": "", "name": "foo", "args": {}}],
                ),
            )

        assert "" not in client._tool_call_id_to_event_id
