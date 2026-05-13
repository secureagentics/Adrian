"""Tests for the in-memory replay buffer and reconnect replay path.

Key design: the happy path (connected + send succeeds) does NOT touch the
ring buffer. The buffer only fills when we detect a disconnect, either
because ``_connected`` is unset on entry, or because ``ws.send`` raises.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import time
from unittest.mock import AsyncMock, MagicMock, patch

from adrian.format.types import AgentContext, PairedEvent, ToolPairData
from adrian.proto import event_pb2 as pb
from adrian.ws import (
    _QUOTA_EXHAUSTED_CLOSE_CODE,
    _QUOTA_RECONNECT_DELAY,
    WebSocketClient,
)


def _make_paired(event_id: str, run_id: str, output: str = "ok") -> PairedEvent:
    """Build a minimal tool PairedEvent for tests."""
    return PairedEvent(
        event_id=event_id,
        invocation_id="inv-1",
        session_id="sess-1",
        run_id=run_id,
        timestamp="2026-01-01T00:00:00Z",
        pair_type="tool",
        agent=AgentContext(agent_id="test-agent"),
        parent=None,
        data=ToolPairData(tool_name="t", input="{}", output=output),
    )


# ---------------------------------------------------------------------------
# Happy path invariant: no buffer growth while connected
# ---------------------------------------------------------------------------


class TestHappyPathDoesNotBuffer:
    """Connected + healthy on_paired_event must not append to the ring."""

    async def test_connected_sends_do_not_buffer(self) -> None:
        """Emit many events on a live socket; ring must stay empty."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=1000
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            for i in range(100):
                await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        # Login + 100 events went over the wire
        assert mock_ws.send.await_count == 101
        # Ring buffer never touched on the happy path
        assert len(client._replay_buffer) == 0
        assert client._replay_buffer_dropped == 0
        assert client._replay_buffer_filled is False


# ---------------------------------------------------------------------------
# Offline buffering
# ---------------------------------------------------------------------------


class TestBufferWhileDisconnected:
    """Events emitted with _connected unset land in the ring."""

    async def _disconnect(self, client: WebSocketClient) -> None:
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1

    async def test_offline_events_are_buffered(self) -> None:
        """on_paired_event during a disconnect appends to the ring."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=10
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        await self._disconnect(client)

        sends_before = mock_ws.send.await_count
        for i in range(3):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))
        # No wire sends happened while disconnected
        assert mock_ws.send.await_count == sends_before
        assert len(client._replay_buffer) == 3

    async def test_ring_fills_and_wraps_while_offline(self) -> None:
        """Emit more than maxlen offline → ring caps + drops counted."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=1000
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        await self._disconnect(client)

        for i in range(1500):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        assert client._replay_buffer.maxlen == 1000
        assert len(client._replay_buffer) == 1000
        assert client._replay_buffer_filled is True
        # 1500 offline events: first 1000 fill, next 500 evict oldest.
        assert client._replay_buffer_dropped == 500

    async def test_first_fill_warn_fires_once(self) -> None:
        """WARN log fires exactly once when the ring first hits maxlen."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=3
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        await self._disconnect(client)

        with patch("adrian.ws.logger") as mock_logger:
            for i in range(10):
                await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        capacity_warnings = [
            call
            for call in mock_logger.warning.call_args_list
            if "reached capacity" in str(call[0][0])
        ]
        assert len(capacity_warnings) == 1


# ---------------------------------------------------------------------------
# Send failure path, the one hot-path edge that buffers
# ---------------------------------------------------------------------------


class TestSendFailureBuffersFailedFrame:
    """When ws.send raises, the frame we tried to send must be buffered
    so the reconnect replay covers it.
    """

    async def test_send_failure_buffers_the_failed_frame(self) -> None:
        mock_ws = AsyncMock()
        # Login succeeds, event send raises
        mock_ws.send.side_effect = [None, ConnectionError("broken pipe")]
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=10
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()
            await client.on_paired_event(_make_paired("evt-bang", "run-bang"))

        # Exactly one frame buffered, the one that failed to send
        assert len(client._replay_buffer) == 1
        buffered = pb.ClientFrame()
        buffered.ParseFromString(client._replay_buffer[0])
        assert buffered.WhichOneof("frame") == "paired_batch"
        assert buffered.paired_batch.events[0].event_id == "evt-bang"


# ---------------------------------------------------------------------------
# Drop counter + reconnect log
# ---------------------------------------------------------------------------


class TestDropCounter:
    async def test_drop_counter_tracks_overflow_and_logs_on_reconnect(
        self,
    ) -> None:
        """Each eviction bumps _replay_buffer_dropped; reconnect logs total."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=3
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        # Go offline, then overflow the buffer
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1

        for i in range(10):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        # Ring filled on the 3rd append; appends 4..10 each evict one.
        assert client._replay_buffer_dropped == 7
        assert len(client._replay_buffer) == 3

        # Force a reconnect and assert the drop count shows up in the logs.
        reconnect_ws = AsyncMock()
        with (
            patch(
                "adrian.ws.websockets.connect",
                AsyncMock(return_value=reconnect_ws),
            ),
            patch("adrian.ws.logger") as mock_logger,
        ):
            await client.connect()

        drop_warnings = [
            call
            for call in mock_logger.warning.call_args_list
            if "dropped" in str(call[0][0]) and "overflow" in str(call[0][0])
        ]
        assert len(drop_warnings) == 1
        assert 7 in drop_warnings[0][0]


# ---------------------------------------------------------------------------
# Replay on reconnect
# ---------------------------------------------------------------------------


class TestReplayOnReconnect:
    """A reconnect must send login first, then every frame in the ring."""

    async def test_replay_ships_offline_events(self) -> None:
        """Events emitted offline must ship in order after reconnect."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=100
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        # Disconnect and emit 50 offline
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1
        for i in range(50):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))
        assert len(client._replay_buffer) == 50

        # Reconnect
        reconnect_ws = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws),
        ):
            await client.connect()

        # 1 login + 50 buffered paired_batch frames on the new socket.
        assert reconnect_ws.send.await_count == 51

    async def test_replay_sends_login_first(self) -> None:
        """First frame after reconnect must be a SessionLogin."""
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=10
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1
        for i in range(3):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        reconnect_ws = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws),
        ):
            await client.connect()

        # First frame must be login, rest must be paired_batch
        first = pb.ClientFrame()
        first.ParseFromString(reconnect_ws.send.await_args_list[0][0][0])
        assert first.WhichOneof("frame") == "login"
        assert first.login.session_id == "sess-1"
        for call in reconnect_ws.send.await_args_list[1:]:
            frame = pb.ClientFrame()
            frame.ParseFromString(call[0][0])
            assert frame.WhichOneof("frame") == "paired_batch"

    async def test_successful_replay_clears_buffer_and_drop_counter(
        self,
    ) -> None:
        """After a successful drain, ring and drop counter are both reset.

        This keeps the buffer semantically bounded to "events for THIS
        outage", once we successfully shipped them, we shouldn't keep
        paying memory or reporting their drop count forever.
        """
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=3
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        # Overflow the buffer while disconnected: 5 offline emits into a
        # size-3 ring → counter 2, ring at 3.
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1
        for i in range(5):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        assert len(client._replay_buffer) == 3
        assert client._replay_buffer_dropped == 2

        reconnect_ws = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws),
        ):
            await client.connect()

        # Ring drained, counter reset, first-fill flag re-armed.
        assert len(client._replay_buffer) == 0
        assert client._replay_buffer_dropped == 0
        assert client._replay_buffer_filled is False

    async def test_second_outage_warn_fires_again_after_successful_drain(
        self,
    ) -> None:
        """First-fill WARN must re-fire on each outage, not once per SDK life.

        Regression guard: the clear-on-success path resets _replay_buffer_filled
        so a subsequent outage that fills the ring reports independently.
        """
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=3
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        def _capacity_warns(mock_logger: MagicMock) -> int:
            return sum(
                1
                for call in mock_logger.warning.call_args_list
                if "reached capacity" in str(call[0][0])
            )

        # --- Outage 1: fill the ring ---
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1

        with patch("adrian.ws.logger") as mock_logger:
            for i in range(5):
                await client.on_paired_event(_make_paired(f"evt-o1-{i}", f"run-o1-{i}"))
            assert _capacity_warns(mock_logger) == 1

        # --- Reconnect drains + clears ---
        reconnect_ws_1 = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws_1),
        ):
            await client.connect()
        assert client._replay_buffer_filled is False

        # --- Outage 2: fill the ring again ---
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1

        with patch("adrian.ws.logger") as mock_logger:
            for i in range(5):
                await client.on_paired_event(_make_paired(f"evt-o2-{i}", f"run-o2-{i}"))
            # Without the flag reset, this would be 0.
            assert _capacity_warns(mock_logger) == 1

    async def test_replay_skips_login_if_already_sent(self) -> None:
        """A concurrent on_paired_event that logged in post-reconnect must
        not cause replay to emit a second login.
        """
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=10
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1
        for i in range(3):
            await client.on_paired_event(_make_paired(f"evt-{i}", f"run-{i}"))

        # Model the race: a live on_paired_event logged in before replay ran.
        client._logged_in = True

        reconnect_ws = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws),
        ):
            await client.connect()

        # Only the 3 paired_batch frames, no login (replay respected the flag)
        assert reconnect_ws.send.await_count == 3
        for call in reconnect_ws.send.await_args_list:
            frame = pb.ClientFrame()
            frame.ParseFromString(call[0][0])
            assert frame.WhichOneof("frame") == "paired_batch"


# ---------------------------------------------------------------------------
# Stable event_id invariant (needed for NATS Nats-Msg-Id dedup)
# ---------------------------------------------------------------------------


class TestStableEventIdOnReplay:
    async def test_replayed_bytes_carry_original_event_id(self) -> None:
        mock_ws = AsyncMock()
        client = WebSocketClient(
            "ws://x", "sess-1", api_key="k", replay_buffer_frames=10
        )

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1
        original_event_id = "evt-stable"
        await client.on_paired_event(_make_paired(original_event_id, "run-1"))

        assert len(client._replay_buffer) == 1

        # Parse the buffered frame and confirm event_id matches
        buffered = pb.ClientFrame()
        buffered.ParseFromString(client._replay_buffer[0])
        assert buffered.WhichOneof("frame") == "paired_batch"
        assert buffered.paired_batch.events[0].event_id == original_event_id

        # Reconnect; second send (index 1, since 0 is login) must carry
        # the same event_id byte-for-byte.
        reconnect_ws = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws),
        ):
            await client.connect()

        assert reconnect_ws.send.await_count == 2
        replayed = pb.ClientFrame()
        replayed.ParseFromString(reconnect_ws.send.await_args_list[1][0][0])
        assert replayed.WhichOneof("frame") == "paired_batch"
        assert replayed.paired_batch.events[0].event_id == original_event_id


# ---------------------------------------------------------------------------
# Reconnect replay ordering: live emits during drain must not overtake
# ---------------------------------------------------------------------------


class TestReconnectReplayOrdering:
    """The across-outage ordering invariant.

    While the reconnect path is draining the replay buffer, any live
    ``on_paired_event`` calls (e.g. from a parallel agent whose
    LangChain loop kept running) must not race onto the wire ahead
    of older pre-outage frames. The ``_replaying`` flag routes live
    sends back into the same deque so they slot in after the pre-
    outage tail and get picked up by the same drain loop.
    """

    async def test_live_emits_during_replay_are_sent_after_buffered(
        self,
    ) -> None:
        """Wire order must be: [pre-outage] → [live-during-replay]."""

        reconnect_ws = AsyncMock()

        # Hook ws.send on the reconnect socket: between sending the
        # first buffered frame and the second, fire a "live" emit
        # that must NOT jump ahead of the remaining buffered frames.
        client = WebSocketClient(
            "ws://x",
            "sess-1",
            api_key="k",
            replay_buffer_frames=100,
        )

        mock_ws_initial = AsyncMock()
        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=mock_ws_initial),
        ):
            await client.connect()

        # Disconnect and emit 3 offline events.
        client._connected.clear()
        client._logged_in = False
        client._disconnected_at = time.monotonic() - 0.1
        for i in range(3):
            await client.on_paired_event(
                _make_paired(f"evt-offline-{i}", f"run-{i}"),
            )
        assert len(client._replay_buffer) == 3

        # On the reconnect socket, after the 2nd non-login send, fire
        # a live emit. With the _replaying guard that emit buffers
        # (and appends to the deque) instead of racing the wire.
        sent_order: list[str] = []
        fired_live = {"done": False}

        async def recording_send(frame_bytes: bytes) -> None:
            frame = pb.ClientFrame()
            frame.ParseFromString(frame_bytes)
            which = frame.WhichOneof("frame")
            if which == "login":
                sent_order.append("login")
                return
            eid = frame.paired_batch.events[0].event_id
            sent_order.append(eid)
            # After the 2nd offline frame has been sent, simulate a
            # concurrent live agent firing a new event. Without
            # _replaying, this would call ws.send directly and
            # interleave with the remaining drain.
            if eid == "evt-offline-1" and not fired_live["done"]:
                fired_live["done"] = True
                await client.on_paired_event(
                    _make_paired("evt-live-during", "run-live"),
                )

        reconnect_ws.send.side_effect = recording_send

        with patch(
            "adrian.ws.websockets.connect",
            AsyncMock(return_value=reconnect_ws),
        ):
            await client.connect()

        # Expected on-wire order:
        #   login, evt-offline-0, evt-offline-1, evt-offline-2, evt-live-during
        # The live event landed LAST, not between offline-1 and offline-2.
        assert sent_order == [
            "login",
            "evt-offline-0",
            "evt-offline-1",
            "evt-offline-2",
            "evt-live-during",
        ]
        # All drained, buffer and flags reset.
        assert len(client._replay_buffer) == 0
        assert client._replaying is False

    async def test_replaying_flag_routes_live_send_to_buffer(self) -> None:
        """With ``_replaying`` set, a live ``_send_frame`` appends to
        the deque and does NOT touch ``ws.send`` directly."""
        client = WebSocketClient(
            "ws://x",
            "sess-1",
            api_key="k",
            replay_buffer_frames=10,
        )
        mock_ws = AsyncMock()

        with patch("adrian.ws.websockets.connect", AsyncMock(return_value=mock_ws)):
            await client.connect()

        # Reset the wire-send counter so we can assert "no new sends
        # during the _replaying window" cleanly regardless of whether
        # a lazy login landed during connect.
        mock_ws.send.reset_mock()

        # Simulate being mid-replay: connected is set, _replaying is set.
        assert client._connected.is_set()
        client._replaying = True

        await client.on_paired_event(_make_paired("evt-live", "run-1"))

        # Must have landed in the buffer, not on the wire.
        assert mock_ws.send.await_count == 0
        assert len(client._replay_buffer) == 1

        # Cleanup so the client's shutdown path doesn't fire against
        # a half-real state machine.
        client._replaying = False


# ---------------------------------------------------------------------------
# Quota-exhausted close → slow reconnect
# ---------------------------------------------------------------------------


class _QuotaClosingWs:
    """Async-iterable stub that closes immediately with a configurable code."""

    def __init__(self, close_code: int) -> None:
        self.close_code = close_code

    def __aiter__(self) -> _QuotaClosingWs:
        return self

    async def __anext__(self) -> bytes:
        raise StopAsyncIteration

    async def close(self, *_args: object, **_kwargs: object) -> None:
        return None


class TestQuotaExhaustedReconnect:
    """A 4003 close arms a one-shot 60s reconnect delay."""

    async def test_quota_close_sets_reconnect_delay(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        client._ws = _QuotaClosingWs(  # type: ignore[assignment]
            close_code=_QUOTA_EXHAUSTED_CLOSE_CODE,
        )
        client._connected.set()

        # Patch ``asyncio.sleep`` so the reconnect task's 60s wait is
        # instant under test, the assertion below runs deterministically
        # and ``client.close()`` cancels any task that's still pending.
        with (
            patch("adrian.ws.websockets.connect", AsyncMock(return_value=AsyncMock())),
            patch("adrian.ws.asyncio.sleep", AsyncMock()),
        ):
            await client._recv_loop()
            assert client._next_reconnect_delay == _QUOTA_RECONNECT_DELAY
            await client.close()

    async def test_normal_close_leaves_reconnect_delay_unset(self) -> None:
        client = WebSocketClient("ws://x", "s", api_key="k")
        # close_code=1000 is the normal "going away" close.
        client._ws = _QuotaClosingWs(close_code=1000)  # type: ignore[assignment]
        client._connected.set()

        with (
            patch("adrian.ws.websockets.connect", AsyncMock(return_value=AsyncMock())),
            patch("adrian.ws.asyncio.sleep", AsyncMock()),
        ):
            await client._recv_loop()
            assert client._next_reconnect_delay is None
            await client.close()

    async def test_connect_honours_pending_reconnect_delay(self) -> None:
        """``connect()`` consumes ``_next_reconnect_delay`` once before the first attempt."""
        client = WebSocketClient("ws://x", "s", api_key="k")
        client._next_reconnect_delay = _QUOTA_RECONNECT_DELAY

        mock_sleep = AsyncMock()

        with (
            patch(
                "adrian.ws.websockets.connect",
                AsyncMock(return_value=AsyncMock()),
            ),
            patch("adrian.ws.asyncio.sleep", mock_sleep),
        ):
            await client.connect()

        # The pre-attempt sleep must have run with the quota delay.
        assert any(
            call.args and call.args[0] == _QUOTA_RECONNECT_DELAY
            for call in mock_sleep.call_args_list
        )
        # And the field is cleared so the next reconnect uses the
        # standard exponential schedule.
        assert client._next_reconnect_delay is None
