"""Async WebSocket ``EventHandler`` that streams ``PairedEvent`` to the worker core API.

Converts each ``PairedEvent`` into a ``pb.PairedEvent`` protobuf, wraps it in a
``ClientFrame.paired_batch``, and sends it over a long-lived WebSocket
connection.  Verdicts received back resolve block-mode futures and fire the
callback handler's verdict processing.

Implements the ``EventHandler`` protocol so it slots into the SDK's hook
registry alongside ``JSONLHandler``.
"""

from __future__ import annotations

import asyncio
import contextlib
import json
import logging
import time
from collections import OrderedDict, deque
from collections.abc import Awaitable, Callable
from typing import TYPE_CHECKING, Any

import websockets

if TYPE_CHECKING:
    from adrian.config import OnDisconnectCallback, OnReconnectCallback
    from adrian.handler import AdrianCallbackHandler

from adrian.format.types import (
    AgentContext,
    LlmPairData,
    PairedEvent,
    ParentContext,
)
from adrian.proto import event_pb2 as pb

logger = logging.getLogger("adrian.ws")

SCHEMA_VERSION = 2

_INITIAL_BACKOFF = 1.0
_MAX_BACKOFF = 30.0
# Server close code: quota exhausted.  Spec'd in
# server/internal/websocket/handler.go (closeQuotaExceeded).  Returning
# every 30s would hammer the server while quota is depleted; one
# minute is slow enough to be cheap, fast enough that the next hourly
# / daily / monthly window-rollover is picked up within tolerance.
_QUOTA_EXHAUSTED_CLOSE_CODE = 4003
_QUOTA_RECONNECT_DELAY = 60.0
# Cap on in-flight LLM run_id → event_id mappings.  Evicted LRU-style;
# block-mode lookups for evicted entries fail open.
_MAX_RUN_ID_MAP = 1024
# Cap on in-flight tool_call_id → event_id mappings (block-mode correlation).
_MAX_TOOL_CALL_MAP = 1024

_DEFAULT_REPLAY_BUFFER_FRAMES = 1000

# Heartbeat tuning.  10s interval / 15s pong timeout detects half-open
# connections (ALB idle cut, NAT drop, dead remote process) without
# flooding the wire.  Kept in sync with the backend's pingInterval /
# pongTimeout, if these change, update server/internal/websocket/handler.go.
_PING_INTERVAL = 10.0
_PING_TIMEOUT = 15.0

_PROVIDER_PREFIXES: dict[str, str] = {
    "chatanthropic": "anthropic",
    "chatopenai": "openai",
    "chatgooglegenai": "google",
    "chatcohere": "cohere",
    "chatmistralai": "mistral",
}

_PAIR_TYPE_MAP: dict[str, pb.PairType.ValueType] = {
    "llm": pb.PAIR_TYPE_LLM,
    "tool": pb.PAIR_TYPE_TOOL,
}


def _derive_provider(model_class_name: str) -> str:
    """Derive the LLM provider from the model class name.

    Args:
        model_class_name: Class name like ``"ChatAnthropic"`` or ``"ChatOpenAI"``.

    Returns:
        Provider string (e.g. ``"anthropic"``), or the class name lower-cased
        if no known prefix matches.
    """
    key = model_class_name.lower()

    return _PROVIDER_PREFIXES.get(key, key)


def _fill_agent_context(
    pb_ctx: pb.AgentContext, src: AgentContext | ParentContext
) -> None:
    """Copy an AgentContext / ParentContext dataclass into its proto counterpart."""
    pb_ctx.agent_id = src.agent_id
    pb_ctx.system_prompt = src.system_prompt
    pb_ctx.user_instruction = src.user_instruction


def _safe_cancel(
    task_or_future: asyncio.Task[Any] | asyncio.Future[Any] | None,
) -> None:
    """Cancel a task / future, ignoring closed-loop errors at shutdown.

    Adrian's ``atexit`` handler may run after the user's loop has been
    closed; in that path ``adrian.shutdown`` spawns a new ``asyncio.run``
    and walks each handler's ``close()``.  Tasks bound to the *old* loop
    can no longer be cancelled (``call_soon`` raises ``Event loop is
    closed``).  Swallowing the error here keeps the cleanup path quiet,
    the task will be reaped when the dead loop is GC'd.
    """
    if task_or_future is None or task_or_future.done():
        return
    # "Event loop is closed", old loop is gone, nothing to cancel.
    with contextlib.suppress(RuntimeError):
        task_or_future.cancel()


def _paired_event_to_proto(event: PairedEvent) -> pb.PairedEvent:
    """Convert a ``PairedEvent`` dataclass into its protobuf form.

    ``parent.agent_id`` empty-string signals "no parent agent".
    ``parent_run_id`` empty-string signals "no parent in run tree".
    """
    proto = pb.PairedEvent(
        event_id=event.event_id,
        invocation_id=event.invocation_id,
        session_id=event.session_id,
        run_id=event.run_id,
        parent_run_id=event.parent_run_id,
        timestamp=event.timestamp,
        pair_type=_PAIR_TYPE_MAP.get(event.pair_type, pb.PAIR_TYPE_UNSPECIFIED),
    )

    _fill_agent_context(proto.agent, event.agent)

    if event.parent is not None:
        _fill_agent_context(proto.parent, event.parent)

    if isinstance(event.data, LlmPairData):
        proto.llm.model = event.data.model

        for msg in event.data.messages:
            pb_msg = proto.llm.messages.add()
            pb_msg.role = msg["role"]
            pb_msg.content = msg["content"]

        proto.llm.output = event.data.output

        for tc in event.data.tool_calls:
            pb_tc = proto.llm.tool_calls.add()
            pb_tc.name = tc["name"]
            pb_tc.args = json.dumps(tc["args"], default=str)
            pb_tc.id = tc["id"]

        if event.data.usage is not None:
            proto.llm.usage.prompt_tokens = event.data.usage["prompt_tokens"]
            proto.llm.usage.completion_tokens = event.data.usage["completion_tokens"]
            proto.llm.usage.total_tokens = event.data.usage["total_tokens"]
    else:
        # Union is LlmPairData | ToolPairData; this branch is the
        # ToolPairData case.
        proto.tool.tool_name = event.data.tool_name
        proto.tool.tool_call_id = event.data.tool_call_id or ""
        proto.tool.input = event.data.input
        proto.tool.output = event.data.output

    if event.metadata:
        proto.metadata_json = json.dumps(event.metadata, default=str).encode()

    return proto


class WebSocketClient:
    """Streams ``PairedEvent`` instances to the worker core API.

    Connects eagerly via :meth:`schedule_connect` with exponential backoff,
    auto-detects the LLM provider on the first LLM pair, sends paired events
    as protobuf frames, and resolves block-mode futures when verdicts arrive.
    """

    def __init__(
        self,
        url: str,
        session_id: str,
        api_key: str,
        handler: AdrianCallbackHandler | None = None,
        on_disconnect: OnDisconnectCallback | None = None,
        on_reconnect: OnReconnectCallback | None = None,
        on_login_ack: Callable[[], Awaitable[None]] | None = None,
        replay_buffer_frames: int = _DEFAULT_REPLAY_BUFFER_FRAMES,
    ) -> None:
        """Initialise without connecting.

        Args:
            url: WebSocket endpoint URL.
            session_id: Session ID sent in the login frame.
            api_key: Adrian API key for the ``Authorization`` header.
            handler: Callback handler for verdict processing.
            on_disconnect: Fired when the connection is lost (sync or async).
                Receives a reason string.
            on_reconnect: Fired when the connection re-establishes after a
                prior disconnect (sync or async).  Does not fire on initial
                connect.
            on_login_ack: Async hook fired after each ``LoginAck`` frame is
                applied, once per (re)connect.  Used internally to push a
                fresh ``McpInventory`` on every login.  Exceptions are
                logged and swallowed.
            replay_buffer_frames: Ring-buffer capacity (frame count, not
                bytes).  When the cap is reached each further append evicts
                the oldest frame; a one-shot WARN fires on first fill, and
                the cumulative drop count is logged at WARN on the next
                reconnect.
        """
        self._url = url
        self._session_id = session_id
        self._api_key = api_key
        self._handler = handler
        self._on_disconnect = on_disconnect
        self._on_reconnect = on_reconnect
        self._on_login_ack_cb = on_login_ack
        self._provider = ""
        self._model = ""
        # Server-supplied execution-mode policy.  Populated when the
        # first ServerFrame{login_ack} arrives after each (re)connect.
        # ``policy_active()``, ``block_timeout()``, and
        # ``fail_closed_on_classifier_error()`` read this state to
        # decide whether the patched ToolNode should wait for a verdict
        # and how to handle classifier failures/timeouts.
        self._mode: int = pb.MODE_UNSPECIFIED
        self._policy: pb.PolicySnapshot | None = None
        # Set the first time a ``ServerFrame{login_ack}`` is applied.
        # Used in two places:
        #   1. ``on_paired_event`` defensively pre-registers a
        #      verdict-wait future when this event is unset, so the
        #      very first tool-bearing LLM emission is covered even
        #      though the recv loop hasn't yet processed LoginAck and
        #      ``policy_active()`` reads False.
        #   2. The patched ``ToolNode.ainvoke`` ``await``s this event
        #      (with a short timeout) before deciding whether to wait
        #      for a verdict, so the first ToolNode invocation cannot
        #      run-through-without-waiting in the same window.
        # Stays set across disconnect/reconnect because mode/policy
        # state survives, a fresh LoginAck on reconnect simply re-sets
        # an already-set event.
        self._login_ack_received: asyncio.Event = asyncio.Event()
        self._ws: websockets.ClientConnection | None = None
        self._logged_in = False
        self._connected = asyncio.Event()
        self._connect_task: asyncio.Task[None] | None = None
        self._recv_task: asyncio.Task[None] | None = None
        # Set by close() so _handle_disconnect knows not to spawn a reconnect
        # during a graceful shutdown.
        self._closing = False
        # Futures awaited by the patched ToolNode.ainvoke when the
        # active mode requires a wait (BLOCK or HITL).  Each resolves
        # with the matching ``Verdict`` proto.  Futures survive a
        # disconnect: a late verdict after reconnect still resolves
        # the wait; if none arrives, ``wait_for_verdict``'s timeout
        # returns None and the patched ToolNode applies the current
        # fail-open/fail-closed classifier-error policy.
        self._pending_verdicts: dict[str, asyncio.Future[pb.Verdict]] = {}
        # Maps LLM pair run_id → event_id so a subsequent tool call can
        # look up the verdict by its parent_run_id (the LLM's run_id).
        # LRU-capped at _MAX_RUN_ID_MAP to bound memory on long sessions.
        self._run_id_to_event_id: OrderedDict[str, str] = OrderedDict()
        # Verdict-correlation map: maps each tool_call.id emitted by
        # an LLM to the event_id of the LLM pair that emitted it.
        # Populated on every LLM PairedEvent that has tool_calls.
        # Consulted by the patched ``ToolNode.ainvoke`` so each tool
        # in a parallel fan-out waits on its own producing LLM's
        # verdict, not a global "last" pointer.  LRU-capped at
        # ``_MAX_TOOL_CALL_MAP``.
        self._tool_call_id_to_event_id: OrderedDict[str, str] = OrderedDict()
        # Serialises the lazy login-then-send sequence so two concurrent
        # on_paired_event calls (parallel agents) cannot both send a login.
        # Reused by _replay_buffer_to_ws to coordinate with live sends.
        self._login_lock = asyncio.Lock()
        # Ring buffer of recently serialised ClientFrame bytes.  Appended
        # only from the offline-or-send-failure paths in _send_frame; the
        # happy path bypasses the ring entirely.  Drained on reconnect.
        self._replay_buffer: deque[bytes] = deque(maxlen=replay_buffer_frames)
        # Flips True on the first append that reaches maxlen.  Gates the
        # one-shot "buffer full" WARN so we don't flood logs.
        self._replay_buffer_filled: bool = False
        # Monotonic counter of frames dropped due to buffer overflow
        # (oldest evicted when a new append arrives at a full ring).
        # Logged at WARN on the next reconnect.
        self._replay_buffer_dropped: int = 0
        # True while the reconnect path is draining the replay buffer.
        # Live sends observed during this window are routed back into
        # the same deque so they slot in AFTER the pre-outage tail
        # rather than racing onto the wire ahead of older buffered
        # frames. Flipped on as the first sync line of
        # _replay_buffer_to_ws and cleared in its finally.
        self._replaying: bool = False
        # Set by _handle_disconnect, cleared on successful reconnect.
        # Used to gate on_reconnect and measure downtime.
        self._disconnected_at: float | None = None
        # One-shot delay applied before the next ``connect()`` attempt.
        # Set when the server closes with a code that requests a longer
        # wait (currently only 4003 quota exhausted); cleared by
        # ``connect()`` after honouring it.  ``None`` means use the
        # standard exponential schedule.
        self._next_reconnect_delay: float | None = None

    # -- Mode / policy state (populated by LoginAck) --

    def policy_active(self) -> bool:
        """Whether the active server mode requires waiting on verdicts.

        Single predicate consulted by the patched ``ToolNode.ainvoke``.
        Returns ``True`` for ``MODE_BLOCK`` and ``MODE_HITL``; ``False``
        for ``MODE_ALERT`` and unset (pre-login) state.
        """
        return self._mode in (pb.MODE_BLOCK, pb.MODE_HITL)

    def block_timeout(self, kwarg_default: float) -> float | None:
        """Effective per-tool-call wait timeout for the active mode.

        - ``MODE_BLOCK``: ``kwarg_default`` (typically 30s). Timeout
          handling follows ``fail_closed_on_classifier_error``.
        - ``MODE_HITL``: ``None``, wait indefinitely for human review.
        - ``MODE_ALERT`` / unset: ``0``, caller short-circuits before
          registering a future.
        """
        if self._mode == pb.MODE_BLOCK:
            return kwarg_default
        elif self._mode == pb.MODE_HITL:
            return None
        else:
            return 0

    def fail_closed_on_classifier_error(self) -> bool:
        """Whether classifier errors/timeouts should halt tool execution."""
        return bool(
            self._policy is not None
            and self._policy.fail_closed_on_classifier_error
        )

    # -- EventHandler protocol --

    async def on_paired_event(self, event: PairedEvent) -> None:
        """Send a paired event over the WebSocket.

        Auto-detects the LLM provider on the first LLM pair, updates the
        run_id → event_id map for block mode, converts the dataclass to
        protobuf, and sends a ``ClientFrame.paired_batch`` frame.

        For LLM pairs that carry tool_calls, registers the verdict-wait
        future *before* the frame leaves the SDK.  This closes the race
        where a fast verdict roundtrip resolves and is dropped before
        the patched ``ToolNode.ainvoke`` reaches its own
        ``register_pending`` call.  The matching ``register_pending``
        from the wait site is a get-or-create that returns the existing
        future.

        Args:
            event: The paired event to stream.
        """
        if (
            event.pair_type == "llm"
            and not self._provider
            and isinstance(event.data, LlmPairData)
        ):
            self._model = event.data.model
            self._provider = _derive_provider(event.data.model)

        if event.pair_type == "llm":
            self._run_id_to_event_id[event.run_id] = event.event_id
            self._run_id_to_event_id.move_to_end(event.run_id)

            if len(self._run_id_to_event_id) > _MAX_RUN_ID_MAP:
                self._run_id_to_event_id.popitem(last=False)

            # Populate tool_call.id → event_id so each tool call can block
            # on its own producing LLM's verdict under parallel fan-out.
            if isinstance(event.data, LlmPairData) and event.data.tool_calls:
                for tc in event.data.tool_calls:
                    tc_id = tc.get("id") or ""

                    if not tc_id:
                        continue

                    self._tool_call_id_to_event_id[tc_id] = event.event_id
                    self._tool_call_id_to_event_id.move_to_end(tc_id)

                    if len(self._tool_call_id_to_event_id) > _MAX_TOOL_CALL_MAP:
                        self._tool_call_id_to_event_id.popitem(last=False)

                # Pre-register the wait future so an eager verdict
                # cannot race ahead of the ToolNode patch.  Gated on
                # ``policy_active()`` so ALERT-mode sessions don't
                # accumulate futures that will never be resolved or
                # awaited, except for the very first event of the
                # session, where ``LoginAck`` may not yet have been
                # processed by the recv loop and ``policy_active()``
                # therefore reads False even when the mode will
                # imminently be set to BLOCK or HITL.  Pre-register
                # defensively in that window; in ALERT mode the gate
                # filters out every subsequent event so the leak is
                # bounded to one orphan future per session.
                if self.policy_active() or not self._login_ack_received.is_set():
                    self.register_pending(event.event_id)

        proto = _paired_event_to_proto(event)
        frame = pb.ClientFrame()
        added = frame.paired_batch.events.add()
        added.CopyFrom(proto)

        await self._send_frame(frame)

    async def close(self) -> None:
        """Cancel background tasks and close the WebSocket.

        Sets ``_closing`` so any in-flight ``_handle_disconnect`` does not
        spawn a reconnect during graceful shutdown.

        Defensive against the ``atexit`` shutdown path: ``adrian.shutdown``
        spawns a fresh ``asyncio.run`` loop after the user's loop has
        already closed, so background tasks bound to the old loop can no
        longer be cancelled cleanly (``call_soon`` raises
        ``Event loop is closed``).  Skip the cancel in that case, the
        old loop is gone, the task will be reaped by GC.
        """
        self._closing = True

        _safe_cancel(self._recv_task)
        self._recv_task = None
        _safe_cancel(self._connect_task)
        self._connect_task = None

        if self._ws is not None:
            with contextlib.suppress(Exception):
                await asyncio.wait_for(self._ws.close(), timeout=2.0)
            self._ws = None

        for fut in self._pending_verdicts.values():
            if not fut.done():
                _safe_cancel(fut)
        self._pending_verdicts.clear()

    # -- Connection lifecycle --

    def schedule_connect(self, loop: asyncio.AbstractEventLoop) -> None:
        """Schedule :meth:`connect` as a background task on the given loop."""
        if self._connect_task is None or self._connect_task.done():
            self._connect_task = loop.create_task(self.connect())

    async def connect(self) -> None:
        """Establish the WebSocket with exponential-backoff retry.

        Heartbeat (``ping_interval`` / ``ping_timeout``) is configured on
        the underlying ``websockets`` client; if the server fails to pong
        within ``_PING_TIMEOUT`` the library closes the connection and
        ``_recv_loop`` surfaces the disconnect via ``_handle_disconnect``.

        On a reconnect (``_disconnected_at`` set by a prior disconnect),
        drains the replay buffer and fires ``on_reconnect``.  Login is
        deferred to ``_send_frame`` / ``_replay_buffer_to_ws`` so the
        auto-detected provider/model is included.  An ``api_key``, if
        configured, is sent as an ``Authorization: Bearer <key>`` header.

        Honours ``_next_reconnect_delay`` if a previous disconnect set
        it (e.g. 4003 quota exhausted requests a slower retry).  The
        delay is consumed on the first attempt; subsequent failures
        fall back to the standard exponential schedule.
        """
        initial_delay = self._next_reconnect_delay
        self._next_reconnect_delay = None

        if initial_delay is not None:
            logger.info(
                "delaying reconnect by %.0fs (server-requested)",
                initial_delay,
            )
            await asyncio.sleep(initial_delay)

        backoff = _INITIAL_BACKOFF
        loop = asyncio.get_running_loop()

        headers: dict[str, str] = {}

        if self._api_key:
            headers["Authorization"] = f"Bearer {self._api_key}"

        while True:
            try:
                self._ws = await websockets.connect(
                    self._url,
                    additional_headers=headers,
                    ping_interval=_PING_INTERVAL,
                    ping_timeout=_PING_TIMEOUT,
                )
                self._connected.set()
                self._recv_task = loop.create_task(self._recv_loop())

                disconnected_at = self._disconnected_at
                is_reconnect = disconnected_at is not None

                if disconnected_at is not None:
                    downtime = time.monotonic() - disconnected_at
                    self._disconnected_at = None
                    logger.warning(
                        "WebSocket reconnected: %s (session_id=%s, downtime=%.2fs)",
                        self._url,
                        self._session_id,
                        downtime,
                    )

                    if self._replay_buffer_dropped > 0:
                        logger.warning(
                            "replay buffer dropped %d frames due to overflow "
                            "before this reconnect (session_id=%s); "
                            "increase replay_buffer_frames if this recurs",
                            self._replay_buffer_dropped,
                            self._session_id,
                        )
                else:
                    logger.info("WebSocket connected: %s", self._url)

                # Drain anything buffered while we were offline, even
                # on the very first connect.  ``_send_mcp_inventory``
                # and other init-time emitters queue frames before the
                # WS is open; without this drain those frames never
                # ship until something else triggers a live send.
                if self._replay_buffer:
                    logger.info(
                        "replaying %d buffered frames after connect",
                        len(self._replay_buffer),
                    )
                    await self._replay_buffer_to_ws()

                if is_reconnect:
                    await self._fire_on_reconnect()

                return
            except Exception:
                logger.warning(
                    "WebSocket connect to %s failed, retrying in %.0fs",
                    self._url,
                    backoff,
                )
                try:
                    await asyncio.sleep(backoff)
                except RuntimeError:
                    # Loop closed mid-retry (atexit shutdown).  Bail out
                    # quietly rather than dumping a traceback.
                    return
                backoff = min(backoff * 2, _MAX_BACKOFF)

    async def _send_login(self, ws: websockets.ClientConnection) -> None:
        """Send the mandatory SessionLogin frame."""
        frame = pb.ClientFrame()
        frame.login.session_id = self._session_id
        frame.login.llm_stack.provider = self._provider
        frame.login.llm_stack.model = self._model
        frame.login.schema_version = SCHEMA_VERSION
        await ws.send(frame.SerializeToString())
        logger.debug(
            "Sent login (session=%s, provider=%s, model=%s, schema=%d)",
            self._session_id,
            self._provider,
            self._model,
            SCHEMA_VERSION,
        )

    async def _send_frame(self, frame: pb.ClientFrame) -> None:
        """Serialise and send a ``ClientFrame``, buffering on failure.

        Happy path (connected + healthy): send over WS, bypass the ring
        entirely, zero overhead.  Offline on entry: buffer for replay.
        During reconnect replay: buffer as well, so the drain loop picks
        this frame up after the pre-outage tail (preserves order across
        the outage boundary).  Send raises: buffer the in-flight frame
        then trigger ``_handle_disconnect`` so state is cleared and
        reconnect is spawned.
        """
        frame_bytes = frame.SerializeToString()
        kind = frame.WhichOneof("frame")

        if not self._connected.is_set() or self._replaying:
            self._buffer_frame(frame_bytes)
            reason = "disconnected" if not self._connected.is_set() else "replaying"
            logger.info(
                "buffered for replay (session_id=%s, kind=%s, "
                "buffer_size=%d, reason=%s)",
                self._session_id,
                kind,
                len(self._replay_buffer),
                reason,
            )

            return

        ws = self._ws

        if ws is None:
            self._buffer_frame(frame_bytes)

            return

        try:
            async with self._login_lock:
                if not self._logged_in:
                    await self._send_login(ws)
                    self._logged_in = True

            await ws.send(frame_bytes)
            logger.debug("Sent %s frame", kind)
        except Exception:
            # Send raised, we cannot confirm the server received this frame.
            # Buffer it so the reconnect replay ships it, then clean up state.
            self._buffer_frame(frame_bytes)
            await self._handle_disconnect("send_failure")

    async def _recv_loop(self) -> None:
        """Read ``ServerFrame``s, dispatch by oneof kind.

        First frame after each (re)login MUST be ``login_ack``; anything
        else is a protocol error and we tear the connection down so the
        reconnect path can try again.  Subsequent frames are
        ``verdict``s.  Unknown oneof kinds (future server additions like
        a quota-exhausted signal) are logged and dropped rather than
        crashing the loop.

        Any exit path (clean close, exception, cancellation) calls
        ``_handle_disconnect`` via ``finally`` so state is cleared and a
        reconnect is spawned.
        """
        ws = self._ws

        if ws is None:
            return

        awaiting_login_ack = True
        try:
            async for message in ws:
                if not isinstance(message, bytes):
                    continue

                frame = pb.ServerFrame()
                frame.ParseFromString(message)
                kind = frame.WhichOneof("frame")

                if awaiting_login_ack:
                    awaiting_login_ack = False
                    if kind != "login_ack":
                        logger.error(
                            "expected ServerFrame{login_ack} as first frame, "
                            "got %r, closing connection",
                            kind,
                        )
                        return

                if kind == "login_ack":
                    self._on_login_ack(frame.login_ack)
                elif kind == "verdict":
                    await self._on_verdict_frame(frame.verdict)
                else:
                    logger.warning(
                        "ignoring unknown ServerFrame kind %r "
                        "(future server addition?)",
                        kind,
                    )
        except asyncio.CancelledError:
            # Expected on graceful shutdown or when _handle_disconnect cancels
            # us from the send_failure path.  Re-raise to honour cancellation.
            raise
        except Exception as exc:
            logger.warning("recv_loop exited: %s", exc)
        finally:
            close_code = getattr(ws, "close_code", None)

            if close_code == _QUOTA_EXHAUSTED_CLOSE_CODE:
                self._next_reconnect_delay = _QUOTA_RECONNECT_DELAY

            reason = (
                f"quota_exhausted (close={close_code})"
                if close_code == _QUOTA_EXHAUSTED_CLOSE_CODE
                else "recv_loop_exit"
            )
            await self._handle_disconnect(reason)

    def _on_login_ack(self, ack: pb.LoginAck) -> None:
        """Apply the org's effective execution-mode policy.

        Fires the ``on_login_ack`` hook (if configured) as a fire-and-forget
        task on the running loop so the recv loop doesn't block waiting on it.
        """
        self._mode = ack.policy.mode
        self._policy = ack.policy
        self._login_ack_received.set()
        logger.info(
            "LoginAck received: mode=%s policy_m0=%s policy_m2=%s "
            "policy_m3=%s policy_m4=%s fail_closed_on_classifier_error=%s",
            pb.Mode.Name(ack.policy.mode),
            ack.policy.policy_m0,
            ack.policy.policy_m2,
            ack.policy.policy_m3,
            ack.policy.policy_m4,
            ack.policy.fail_closed_on_classifier_error,
        )

        if self._on_login_ack_cb is not None:
            asyncio.create_task(self._run_login_ack_cb())

    async def _run_login_ack_cb(self) -> None:
        """Invoke the on_login_ack hook, swallowing exceptions."""
        if self._on_login_ack_cb is None:
            return
        try:
            await self._on_login_ack_cb()
        except Exception:
            logger.exception("on_login_ack hook raised")

    async def _on_verdict_frame(self, verdict: pb.Verdict) -> None:
        """Fire callbacks then resolve the matching pending future, if any.

        The future is left in ``_pending_verdicts`` after ``set_result`` so
        a later ``register_pending`` (e.g. from the patched ToolNode after
        the verdict has already round-tripped) returns the resolved
        future and the wait completes immediately.  ``wait_for_verdict``
        owns the cleanup: its ``finally`` pops the entry after the await
        returns.
        """
        if verdict.HasField("policy"):
            # Keep the policy snapshot fresh for BLOCK-mode timeout
            # decisions. Execution mode remains login-fixed for this
            # release; hot-switching mode mid-session is out of scope.
            self._policy = verdict.policy

        logger.info(
            "Verdict received: event_id=%s mad_code=%s status=%s mode=%s hitl=%s",
            verdict.event_id,
            verdict.mad_code or "-",
            pb.VerdictStatus.Name(verdict.status),
            pb.Mode.Name(verdict.policy.mode),
            verdict.HasField("hitl"),
        )

        if self._handler is not None:
            await self._handler.handle_verdict(verdict)

        fut = self._pending_verdicts.get(verdict.event_id)

        if fut is None:
            if verdict.HasField("hitl"):
                logger.warning(
                    "HITL resolution for unknown event_id=%s, ignoring "
                    "(stale resolution from a prior SDK process)",
                    verdict.event_id,
                )
            return

        if not fut.done():
            fut.set_result(verdict)

    # -- Resilience: buffering, replay, disconnect/reconnect --

    def _buffer_frame(self, frame_bytes: bytes) -> None:
        """Append a serialised frame to the replay ring.

        Tracks overflow drops and fires the one-shot "buffer full" WARN.
        Called only from the offline or send-failure paths in
        ``_send_frame``, the happy path bypasses the ring entirely.
        """
        if len(self._replay_buffer) == self._replay_buffer.maxlen:
            self._replay_buffer_dropped += 1

        self._replay_buffer.append(frame_bytes)

        if (
            not self._replay_buffer_filled
            and len(self._replay_buffer) == self._replay_buffer.maxlen
        ):
            self._replay_buffer_filled = True
            logger.warning(
                "adrian replay buffer reached capacity (%d frames); "
                "further frames will evict oldest.  Tune via "
                "replay_buffer_frames or ADRIAN_REPLAY_BUFFER_FRAMES.",
                self._replay_buffer.maxlen,
            )

    async def _replay_buffer_to_ws(self) -> None:
        """Reissue buffered frames over the current WebSocket.

        Sends ``SessionLogin`` first if not already logged in (the server
        requires it as the first frame on every new connection).  Uses
        ``_login_lock`` so a concurrent live send does not race on the
        login check.

        Drains the deque one frame at a time via ``popleft`` inside a
        ``while`` loop, rather than taking a snapshot up front.  That
        way, a live ``_send_frame`` call observed during the drain
        routes its frame to the back of the same deque (because
        ``_replaying`` is set) and this loop picks it up in the next
        iteration, preserving across-outage order
        ``[pre-outage] → [live during replay] → [post-replay live]``.

        On a mid-drain send failure, the failed frame is put back at
        the front with ``appendleft`` and the function returns; the
        next reconnect resumes from exactly where this one stopped.
        """
        ws = self._ws

        if ws is None:
            return

        self._replaying = True
        try:
            async with self._login_lock:
                if not self._logged_in:
                    try:
                        await self._send_login(ws)
                        self._logged_in = True
                    except Exception as exc:
                        logger.warning(
                            "replay aborted: login send failed: %s",
                            exc,
                        )

                        return

            sent = 0
            while self._replay_buffer:
                frame_bytes = self._replay_buffer.popleft()
                try:
                    await ws.send(frame_bytes)
                except Exception as exc:
                    # Put the failed frame back at the front so the next
                    # reconnect's drain resumes from exactly this point.
                    self._replay_buffer.appendleft(frame_bytes)
                    logger.warning(
                        "replay aborted after %d frame(s), %d remaining: %s",
                        sent,
                        len(self._replay_buffer),
                        exc,
                    )

                    return
                sent += 1

            logger.info("replayed %d buffered frames", sent)
            self._replay_buffer_dropped = 0
            self._replay_buffer_filled = False
        finally:
            self._replaying = False

    async def _handle_disconnect(self, reason: str) -> None:
        """Clear connection state and spawn a reconnect.

        Idempotent: if already disconnected or closing, returns immediately.
        Pending verdict futures are intentionally left pending across the
        disconnect, a late verdict after reconnect resolves them; if none
        arrives, ``wait_for_verdict``'s timeout fires naturally.
        """
        if self._closing or not self._connected.is_set():
            return

        self._connected.clear()
        self._disconnected_at = time.monotonic()

        # Only cancel the recv task if we are not currently running inside it.
        # When _recv_loop's own finally calls us, self._recv_task IS the
        # current task, cancelling it would raise CancelledError inside the
        # finally and prevent us from finishing disconnect handling.
        current = asyncio.current_task()

        if self._recv_task is not None and self._recv_task is not current:
            self._recv_task.cancel()

        self._recv_task = None
        self._ws = None
        self._logged_in = False

        logger.warning(
            "disconnected (session_id=%s, reason=%s, pending_verdicts=%d)",
            self._session_id,
            reason,
            len(self._pending_verdicts),
        )

        await self._fire_on_disconnect(reason)

        if self._closing:
            return

        loop = asyncio.get_running_loop()

        if self._connect_task is None or self._connect_task.done():
            self._connect_task = loop.create_task(self.connect())

    async def _fire_on_disconnect(self, reason: str) -> None:
        """Invoke the on_disconnect callback, catching any exception."""
        if self._on_disconnect is None:
            return

        try:
            result = self._on_disconnect(reason)

            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("on_disconnect callback raised")

    async def _fire_on_reconnect(self) -> None:
        """Invoke the on_reconnect callback, catching any exception."""
        if self._on_reconnect is None:
            return

        try:
            result = self._on_reconnect()

            if asyncio.iscoroutine(result):
                await result
        except Exception:
            logger.exception("on_reconnect callback raised")

    # -- Verdict-wait support --

    def register_pending(
        self,
        event_id: str,
    ) -> asyncio.Future[pb.Verdict]:
        """Return a future awaiting a verdict for ``event_id``.

        Reuses an existing pending future if one is already registered,
        so concurrent callers waiting on the same event_id see the same
        verdict once it arrives.  Must be called BEFORE sending the event
        to avoid the race where the verdict arrives before the future exists.
        """
        existing = self._pending_verdicts.get(event_id)

        if existing is not None:
            return existing

        loop = asyncio.get_running_loop()
        fut: asyncio.Future[pb.Verdict] = loop.create_future()
        self._pending_verdicts[event_id] = fut

        return fut

    async def wait_for_verdict(
        self,
        event_id: str,
        timeout: float | None,
    ) -> pb.Verdict | None:
        """Wait for a verdict for ``event_id``.

        ``timeout`` is mode-derived (see :meth:`block_timeout`):
        a positive float for ``MODE_BLOCK`` (caller applies policy at timeout),
        ``None`` for ``MODE_HITL`` (wait indefinitely).  Returns the
        verdict, or ``None`` on timeout.

        Cleans up the ``_pending_verdicts`` entry on either path:
        ``_on_verdict_frame`` only resolves the future, the dict
        ownership belongs here so a late ``register_pending`` after the
        verdict has already arrived can still find the resolved future.
        """
        fut = self.register_pending(event_id)

        try:
            return await asyncio.wait_for(fut, timeout=timeout)
        except TimeoutError:
            logger.warning(
                "Verdict timeout for event_id=%s after %ss",
                event_id,
                timeout,
            )

            return None
        finally:
            self._pending_verdicts.pop(event_id, None)

    async def wait_for_tool_verdict(
        self,
        parent_run_id: str,
        timeout: float | None,
    ) -> pb.Verdict | None:
        """Wait for the verdict of the LLM pair that produced this tool call.

        Looks up the LLM event_id from the run_id map and awaits its verdict.
        Returns ``None`` (fail-open) when the parent LLM has not been seen,
        e.g. tools invoked outside an LLM flow.
        """
        event_id = self._run_id_to_event_id.get(parent_run_id)

        if event_id is None:
            logger.debug(
                "No LLM context for parent_run_id=%s, skipping verdict wait",
                parent_run_id,
            )

            return None

        return await self.wait_for_verdict(event_id, timeout)

    async def wait_for_tool_call_verdict(
        self,
        tool_call_id: str,
        timeout: float | None,
    ) -> pb.Verdict | None:
        """Wait for the verdict of the LLM pair that emitted ``tool_call_id``.

        Every tool call in an AIMessage carries the id the LLM assigned
        to it; that id is threaded through LangChain to the ToolNode
        invocation.  Looking it up against ``_tool_call_id_to_event_id``
        gives the producing LLM's event_id, correct under parallel
        agents where a ``last_llm_event_id``-style global would race.

        Returns ``None`` (fail-open) when ``tool_call_id`` is empty or
        unknown (direct ToolNode invocation, pre-LLM tool, or the LLM
        pair that produced it was evicted from the LRU map).
        """
        if not tool_call_id:
            return None

        event_id = self._tool_call_id_to_event_id.get(tool_call_id)

        if event_id is None:
            logger.debug(
                "No LLM context for tool_call_id=%s, skipping verdict wait",
                tool_call_id,
            )

            return None

        return await self.wait_for_verdict(event_id, timeout)
