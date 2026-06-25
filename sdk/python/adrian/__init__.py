"""Adrian: multi-agent event capture SDK for LangChain/LangGraph as of 2026-05-10.

Initialise with a single call and all LLM / tool activity is automatically
captured, paired, and emitted as ``PairedEvent`` objects through registered
handlers::

    import adrian

    adrian.init(api_key="...")

Events are paired (chat_model_start + llm_end, tool_start + tool_end),
enriched with agent identity and parent context, and emitted through
pluggable handlers (JSONL, WebSocket, custom).

"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import asyncio
import atexit
import logging
import os
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _dist_version
from pathlib import Path
from typing import Any

from adrian.anthropic_handler import anthropic_invocation, anthropic_invocation_sync
from adrian.config import (
    AdrianConfig,
    OnAuditCallback,
    OnBlockCallback,
    OnDisconnectCallback,
    OnEventCallback,
    OnMcpServerCallback,
    OnReconnectCallback,
    OnVerdictCallback,
    get_config,
    is_initialized,
    set_config,
)
from adrian.context import AgentContextTracker
from adrian.format.types import PairedEvent
from adrian.handler import AdrianCallbackHandler
from adrian.handlers.jsonl import JSONLHandler
from adrian.hooks import EventHandler, HookRegistry
from adrian.langchain_handler import (
    _extract_tool_calls as _extract_tool_calls,  # pyright: ignore[reportPrivateUsage]
)
from adrian.langchain_handler import (
    patch_langchain as _patch,
)
from adrian.mcp import (
    McpServer,
    _patch_mcp_adapter,  # pyright: ignore[reportPrivateUsage]
    mcp_servers,
)
from adrian.mcp import (
    _reset as _reset_mcp,  # pyright: ignore[reportPrivateUsage]
)
from adrian.pairing import EventPairBuffer
from adrian.pii import (
    PiiConfig,
    PiiRedactor,
    RedactingHandler,
    RedactionStrategy,
    redact_text,
)
from adrian.proto import event_pb2 as pb
from adrian.session_persistence import resolve_session_id
from adrian.types import ToolCallRecord, VerdictContext
from adrian.ws import WebSocketClient

try:
    __version__ = _dist_version("adrian-sdk")
except PackageNotFoundError:  # running from a source tree that isn't installed
    __version__ = "0.0.0+unknown"
__all__ = [
    "init",
    "shutdown",
    "get_handler",
    "patch_langchain",
    "AdrianCallbackHandler",
    "AdrianConfig",
    "EventHandler",
    "JSONLHandler",
    "McpServer",
    "OnAuditCallback",
    "OnBlockCallback",
    "OnDisconnectCallback",
    "OnEventCallback",
    "OnMcpServerCallback",
    "OnReconnectCallback",
    "OnVerdictCallback",
    "PairedEvent",
    "PiiConfig",
    "PiiRedactor",
    "RedactingHandler",
    "RedactionStrategy",
    "ToolCallRecord",
    "VerdictContext",
    "__version__",
    "mcp_servers",
    "redact_text",
    "patch_anthropic",
    "anthropic_invocation",
    "anthropic_invocation_sync",
]

logger = logging.getLogger("adrian")

_hooks: HookRegistry | None = None
_handler: AdrianCallbackHandler | None = None
_ws_client: WebSocketClient | None = None
_fork_handler_registered: bool = False


# ------------------------------------------------------------------
# Fork safety
# ------------------------------------------------------------------


def _reset_after_fork() -> None:
    """Drop inherited Adrian state in a forked child process.

    Registered via ``os.register_at_fork`` on the first :func:`init` call.
    Nulls out module globals so the child does not silently share the
    parent's WebSocket socket, writing to a shared socket from two
    processes interleaves bytes on the wire, corrupting frames the
    server cannot parse.

    Triggered by pre-fork deployments (``gunicorn --preload``,
    ``multiprocessing.Pool``, Celery prefork).  The child must call
    :func:`init` again from its worker startup hook to establish its
    own connection.
    """
    global _hooks, _handler, _ws_client  # noqa: PLW0603

    _hooks = None
    _handler = None
    _ws_client = None
    _reset_mcp()


# ------------------------------------------------------------------
# Public API
# ------------------------------------------------------------------


def init(
    api_key: str | None = None,
    log_file: str | Path = "events.jsonl",
    handlers: list[EventHandler] | None = None,
    auto_instrument: bool = True,
    log_level: str | None = None,
    ws_url: str | None = None,
    session_id: str | None = None,
    block_timeout: float = 30.0,
    on_event: OnEventCallback | None = None,
    on_verdict: OnVerdictCallback | None = None,
    on_block: OnBlockCallback | None = None,
    on_audit: OnAuditCallback | None = None,
    on_disconnect: OnDisconnectCallback | None = None,
    on_reconnect: OnReconnectCallback | None = None,
    on_mcp_server: OnMcpServerCallback | None = None,
    replay_buffer_frames: int = 1000,
) -> None:
    """Initialise the Adrian SDK.

    Creates the event pairing buffer, agent context tracker, and hook
    registry, then monkey-patches LangChain so every LLM call and tool
    invocation is captured as a ``PairedEvent``.

    Events are emitted through registered handlers. If no handlers are
    provided, defaults to a ``JSONLHandler`` writing to ``log_file``.

    Transport (WebSocket, HTTP, etc.) is not managed by the SDK, pass
    a pre-configured handler via the ``handlers`` list instead.

    Args:
        api_key: Adrian API key.  Falls back to ``ADRIAN_API_KEY`` env
            var.  Stored in config for handlers that need it.
        log_file: Path to the JSONL output file (used when no handlers
            are explicitly provided).
        handlers: List of ``EventHandler`` instances to receive paired
            events. If ``None``, defaults to ``JSONLHandler(log_file)``.
        auto_instrument: Patch LangChain / LangGraph at import time.
        log_level: Optional override for the ``adrian`` logger's level.
            ``None`` (default) inherits from the application's logging
            config; pass e.g. ``"DEBUG"`` to force-enable verbose SDK
            logging without touching global config.
        ws_url: WebSocket URL for the Adrian server (e.g.
            ``"ws://localhost:8080/ws"``).  Falls back to ``ADRIAN_WS_URL``.
            When set and ``handlers`` is ``None``, a ``WebSocketClient`` is
            auto-registered alongside the default ``JSONLHandler``.  Requires
            ``api_key``.
        session_id: Session identifier.  Falls back to
            ``ADRIAN_SESSION_ID``, then to a per-cwd persistent UUID.
            See :mod:`adrian.session_persistence`.
        block_timeout: Max seconds to wait for a verdict in ``MODE_BLOCK``
            before fail-open.  Ignored in ``MODE_ALERT`` (no wait) and
            ``MODE_HITL`` (wait indefinitely).  Falls back to
            ``ADRIAN_BLOCK_TIMEOUT``.
        on_event: Callback for every paired event.
        on_verdict: Callback for every verdict.
        on_block: Callback for BLOCK-tier verdicts (M3 / M4).  Notification
            only; return value is ignored.
        on_audit: Callback for NOTIFY-tier verdicts (M2).
        on_disconnect: Callback fired when the WebSocket is lost.  Receives
            a reason string.  Sync or async.
        on_reconnect: Callback fired when the WebSocket reconnects after a
            prior disconnect.  Does not fire on initial connection.  Sync
            or async.
        on_mcp_server: Callback fired when an MCP server is registered or
            updated.  Receives the freshly-registered ``McpServer``.  Does
            NOT fire on no-op re-observations.  Sync or async.
        replay_buffer_frames: Max serialised frames kept in the in-memory
            ring for replay after a transient WS outage (server restart,
            ALB shuffle).  Each frame is one ``ClientFrame.paired_batch``
            (~4KB).  Default 1000 frames ≈ ~4MB RAM.  Falls back to
            ``ADRIAN_REPLAY_BUFFER_FRAMES``.  At capacity each further
            append evicts the oldest; a one-shot WARN fires on first fill
            and cumulative drops are logged on the next reconnect.
    """
    global _hooks, _handler, _ws_client, _fork_handler_registered  # noqa: PLW0603

    if not _fork_handler_registered and hasattr(os, "register_at_fork"):
        os.register_at_fork(after_in_child=_reset_after_fork)
        _fork_handler_registered = True

    try:
        loop: asyncio.AbstractEventLoop | None = asyncio.get_running_loop()
    except RuntimeError:
        loop = None

    resolved_key = api_key or os.getenv("ADRIAN_API_KEY") or None
    resolved_file = Path(os.getenv("ADRIAN_LOG_FILE", str(log_file)))
    resolved_ws_url = os.getenv("ADRIAN_WS_URL") or ws_url or "ws://localhost:8080/ws"
    resolved_session = (
        os.getenv("ADRIAN_SESSION_ID") or session_id or resolve_session_id()
    )
    resolved_block_timeout = float(
        os.getenv("ADRIAN_BLOCK_TIMEOUT", str(block_timeout)),
    )

    resolved_replay_buffer_frames = replay_buffer_frames
    env_replay = os.getenv("ADRIAN_REPLAY_BUFFER_FRAMES", "").strip()

    if env_replay:
        try:
            resolved_replay_buffer_frames = int(env_replay)
        except ValueError:
            logger.warning(
                "ADRIAN_REPLAY_BUFFER_FRAMES=%r is not an int; "
                "falling back to kwarg default %d",
                env_replay,
                replay_buffer_frames,
            )

    if resolved_ws_url and not resolved_key:
        logger.warning(
            "ws_url is set but no api_key provided.  Set api_key or "
            "ADRIAN_API_KEY; the server will reject the WS connection."
        )

    config = AdrianConfig(
        api_key=resolved_key,
        log_file=resolved_file,
        log_level=log_level,
        session_id=resolved_session,
        ws_url=resolved_ws_url,
        block_timeout=resolved_block_timeout,
        on_event=on_event,
        on_verdict=on_verdict,
        on_block=on_block,
        on_audit=on_audit,
        on_disconnect=on_disconnect,
        on_reconnect=on_reconnect,
        on_mcp_server=_make_on_mcp_server_chain(on_mcp_server),
        replay_buffer_frames=resolved_replay_buffer_frames,
    )

    set_config(config)

    if log_level is not None:
        # Only override the adrian logger's level when the caller asks
        # for it explicitly. Default behaviour respects whatever the
        # application configured via logging.basicConfig / .config.
        logging.getLogger("adrian").setLevel(
            getattr(logging, log_level.upper(), logging.INFO),
        )

    # Build handler list, then optionally wrap with PII redaction
    handler_list: list[EventHandler] = []

    if handlers:
        handler_list = list(handlers)
    else:
        handler_list.append(JSONLHandler(path=resolved_file))

        if resolved_ws_url:
            _ws_client = WebSocketClient(
                url=resolved_ws_url,
                session_id=config.session_id,
                api_key=resolved_key or "",
                on_disconnect=on_disconnect,
                on_reconnect=on_reconnect,
                on_login_ack=_send_mcp_inventory,
                replay_buffer_frames=resolved_replay_buffer_frames,
            )
            handler_list.append(_ws_client)

    handler_list = [RedactingHandler(h) for h in handler_list]

    # Create hook registry and register handlers
    _hooks = HookRegistry()

    for h in handler_list:
        _hooks.register(h)

    # Create pairing and context tracking components
    pair_buffer = EventPairBuffer()
    context_tracker = AgentContextTracker()

    # Create handler with new components
    _handler = AdrianCallbackHandler(
        pair_buffer=pair_buffer,
        context_tracker=context_tracker,
        hooks=_hooks,
        config=config,
    )

    if _ws_client is not None:
        # Back-reference so the recv loop can dispatch verdicts into the
        # handler's block/audit/verdict callback machinery.
        _ws_client._handler = _handler  # pyright: ignore[reportPrivateUsage]

        if loop is not None:
            _ws_client.schedule_connect(loop)
        else:
            logger.debug(
                "No running event loop at init(); WebSocket will connect on "
                "first send from within an async context."
            )

    if auto_instrument:
        _auto_instrument_langchain()
        _auto_instrument_anthropic()

    # MCP server tracking is independent of LangChain auto-instrumentation,
    # it observes a different library (langchain-mcp-adapters) and is the
    # only path the SDK has to learn about MCP servers.  Always run.
    _patch_mcp_adapter()

    atexit.register(shutdown)
    logger.info(
        "Adrian v%s initialised (handlers=%d, ws=%s)",
        __version__,
        len(_hooks),
        resolved_ws_url or "disabled",
    )


def shutdown() -> None:
    """Close all handlers and reset state."""
    global _hooks, _handler, _ws_client  # noqa: PLW0603

    if _hooks is not None:
        try:
            loop = asyncio.get_running_loop()
            loop.create_task(_hooks.close())
        except RuntimeError:
            asyncio.run(_hooks.close())

        _hooks = None

    _handler = None
    _ws_client = None
    set_config(None)


def patch_anthropic() -> None:
    """Apply Anthropic SDK instrumentation.

    Monkey-patches ``anthropic.Anthropic`` and ``anthropic.AsyncAnthropic`` so
    that every ``messages.create`` call is captured as an Adrian ``PairedEvent``.
    Called automatically by :func:`init` when ``auto_instrument=True``.

    Call explicitly only when ``auto_instrument=False``::

        adrian.init(api_key="...", auto_instrument=False)
        adrian.patch_anthropic()
    """
    from adrian.anthropic_handler import patch_anthropic as _patch

    _patch(
        hooks_getter=lambda: _hooks,
        config_getter=lambda: get_config() if is_initialized() else None,
    )


def get_handler() -> AdrianCallbackHandler | None:
    """Return the SDK's callback handler, or ``None`` if uninitialised.

    Useful when ``adrian.init(auto_instrument=False)`` is set and you
    need to attach the handler to LangChain calls explicitly, e.g.::

        adrian.init(api_key=..., auto_instrument=False)
        handler = adrian.get_handler()
        await llm.ainvoke(prompt, config={"callbacks": [handler]})

    The handler is wired into Adrian's WS hook chain at ``init()``
    time; constructing a fresh ``AdrianCallbackHandler`` directly will
    not emit events.
    """
    return _handler


# ------------------------------------------------------------------
# Internal helpers
# ------------------------------------------------------------------


def _get_callback_handler() -> AdrianCallbackHandler | None:
    """Return the current callback handler (closure helper)."""
    return _handler


def _get_config() -> AdrianConfig | None:
    """Return the current config without raising (closure helper)."""
    if not is_initialized():
        return None

    return get_config()


async def _send_mcp_inventory() -> None:
    """Send the current MCP server registry as a ``ClientFrame``.

    Triggers: once per connect (after each ``LoginAck``) and on every
    ``on_mcp_server`` registry change.  The server replaces its full
    list on every frame, so a fresh snapshot is correct on every fire.
    No-op when the WebSocket transport is disabled or when the registry
    is empty (the registry is additive, so an empty snapshot is
    indistinguishable from "not yet observed", sending it would only
    log a ``which=<nil>`` warning on the server).
    """
    ws = _ws_client

    if ws is None:
        return

    servers = mcp_servers()

    if not servers:
        return

    frame = pb.ClientFrame()

    for server in servers:
        added = frame.mcp_inventory.servers.add()
        added.name = server.name
        added.transport = server.transport
        added.endpoint = server.endpoint

    await ws._send_frame(frame)  # pyright: ignore[reportPrivateUsage]


def _make_on_mcp_server_chain(
    user_cb: OnMcpServerCallback | None,
) -> OnMcpServerCallback:
    """Compose ``_send_mcp_inventory`` with the user's ``on_mcp_server``.

    Schedules the inventory sync as a fire-and-forget task on the
    running loop (if any) and forwards transparently to the user's
    callback so its sync-vs-async return shape is preserved for
    :func:`adrian.callbacks.fire` to handle.  When no loop is running,
    the inventory sync is skipped, the next ``LoginAck`` (which only
    fires once a loop is up) will catch up.
    """

    def chain(server: McpServer) -> Any:  # noqa: ANN401
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            pass
        else:
            loop.create_task(_send_mcp_inventory())

        if user_cb is None:
            return None

        return user_cb(server)

    return chain


def patch_langchain() -> None:
    """Apply LangChain / LangGraph instrumentation.

    Monkey-patches ``Runnable``, ``CallbackManager``, ``BaseChatModel``,
    LangGraph ``Pregel`` / ``ToolNode``, ``BaseTool`` (the universal verdict
    gate) and ``AgentExecutor`` so that all LLM and tool activity is captured
    and, under MODE_BLOCK / MODE_HITL, gated on the classifier verdict.
    Called automatically by :func:`init` when ``auto_instrument=True``.

    Call explicitly only when ``auto_instrument=False``::

        adrian.init(api_key="...", auto_instrument=False)
        adrian.patch_langchain()
    """
    _patch(
        handler_getter=_get_callback_handler,
        ws_getter=lambda: _ws_client,
        config_getter=_get_config,
    )


# ------------------------------------------------------------------
# Auto-instrumentation
# ------------------------------------------------------------------


def _auto_instrument_anthropic() -> None:
    """Apply Anthropic SDK monkey-patches if the package is installed."""
    try:
        patch_anthropic()
        logger.debug("Anthropic auto-instrumentation applied")
    except Exception:
        logger.exception("Anthropic auto-instrumentation failed")


def _auto_instrument_langchain() -> None:
    """Apply LangChain / LangGraph monkey-patches if the libraries are present."""
    try:
        patch_langchain()
    except Exception:
        logger.exception("LangChain auto-instrumentation failed")
