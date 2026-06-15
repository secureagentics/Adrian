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
from pathlib import Path
from typing import Any
from uuid import uuid4

from langchain_core.callbacks.manager import CallbackManager
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables.base import Runnable
from langchain_core.runnables.config import ensure_config

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
from adrian.context import AgentContextTracker, get_invocation_id, set_invocation_id
from adrian.format.types import PairedEvent
from adrian.handler import AdrianCallbackHandler
from adrian.handlers.jsonl import JSONLHandler
from adrian.hooks import EventHandler, HookRegistry
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

__version__ = "1.0.2"
__all__ = [
    "init",
    "shutdown",
    "get_handler",
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
    # Default to the hosted Adrian backend so `adrian.init(api_key=...)`
    # Just Works for freemium users. Self-hosted users override via
    # ws_url= or ADRIAN_WS_URL.
    resolved_ws_url = (
        os.getenv("ADRIAN_WS_URL") or ws_url or "wss://adrian.secureagentics.ai/ws"
    )
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


def _inject_callbacks(config: Any) -> Any:  # noqa: ANN401
    """Merge the Adrian handler into a LangChain ``RunnableConfig``.

    Args:
        config: An existing LangChain RunnableConfig or ``None``.

    Returns:
        A config dict guaranteed to contain the Adrian handler.
    """
    handler = _get_callback_handler()

    if handler is None:
        return ensure_config(config)

    config = ensure_config(config)
    callbacks = config.get("callbacks") or []

    if hasattr(callbacks, "handlers"):
        callbacks = list(callbacks.handlers)  # pyright: ignore[reportAttributeAccessIssue]
    elif not isinstance(callbacks, list):
        callbacks = [callbacks] if callbacks else []
    else:
        callbacks = list(callbacks)

    handler_types = [type(h).__name__ for h in callbacks]

    if "AdrianCallbackHandler" not in handler_types:
        callbacks.insert(0, handler)

    config["callbacks"] = callbacks

    return config


# ------------------------------------------------------------------
# Auto-instrumentation
# ------------------------------------------------------------------


def _auto_instrument_langchain() -> None:
    """Apply all monkey-patches to LangChain / LangGraph."""
    try:
        _patch_runnable()
        _patch_callback_manager()
        _patch_chat_model()
        _patch_langgraph()
        _patch_tool_node()
        _patch_base_tool()
        _patch_agent_executor()
        logger.debug("LangChain auto-instrumentation applied")
    except ImportError:
        logger.debug("LangChain not found, skipping auto-instrumentation")
    except Exception:
        logger.exception("Auto-instrumentation failed")


# --- 1. Runnable ---


def _patch_runnable() -> None:
    """Patch ``Runnable.invoke`` / ``ainvoke`` / ``astream`` / ``stream``."""
    if getattr(Runnable, "_adrian_patched", False):
        return

    original_invoke = Runnable.invoke
    original_ainvoke = Runnable.ainvoke
    original_astream = Runnable.astream
    original_stream = Runnable.stream

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        return original_invoke(self, input, config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        return await original_ainvoke(self, input, config, **kwargs)

    async def patched_astream(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        async for chunk in original_astream(self, input, config, **kwargs):
            yield chunk

    def patched_stream(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        yield from original_stream(self, input, config, **kwargs)

    Runnable.invoke = patched_invoke  # type: ignore[assignment]
    Runnable.ainvoke = patched_ainvoke  # type: ignore[assignment]
    Runnable.astream = patched_astream  # type: ignore[assignment]
    Runnable.stream = patched_stream  # type: ignore[assignment]
    Runnable._adrian_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched Runnable.invoke / ainvoke")


# --- 2. CallbackManager ---


def _patch_callback_manager() -> None:
    """Patch ``CallbackManager.__init__`` to always include Adrian."""
    if getattr(CallbackManager, "_adrian_cbm_patched", False):
        return

    original_configure = CallbackManager.configure

    def patched_configure(
        _cls: Any,  # noqa: ANN401
        inheritable_callbacks: Any = None,  # noqa: ANN401
        local_callbacks: Any = None,  # noqa: ANN401
        verbose: bool = False,
        inheritable_tags: Any = None,  # noqa: ANN401
        local_tags: Any = None,  # noqa: ANN401
        inheritable_metadata: Any = None,  # noqa: ANN401
        local_metadata: Any = None,  # noqa: ANN401
        **extra: Any,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        """Inject Adrian handler into inheritable callbacks.

        ``**extra`` forwards any kwargs newer langchain-core releases
        add to ``CallbackManager.configure`` (e.g. 1.3 added
        ``langsmith_inheritable_metadata``) so the patch stays
        forward-compatible without re-declaring every signature change.
        """
        handler = _get_callback_handler()

        if handler:
            if inheritable_callbacks is None:
                inheritable_callbacks = [handler]
            elif isinstance(inheritable_callbacks, list):
                handler_types = [type(h).__name__ for h in inheritable_callbacks]

                if "AdrianCallbackHandler" not in handler_types:
                    inheritable_callbacks = [handler, *inheritable_callbacks]
            elif hasattr(inheritable_callbacks, "handlers"):
                handler_types = [
                    type(h).__name__ for h in inheritable_callbacks.handlers
                ]

                if "AdrianCallbackHandler" not in handler_types:
                    inheritable_callbacks.handlers.insert(0, handler)

        return original_configure(
            inheritable_callbacks=inheritable_callbacks,
            local_callbacks=local_callbacks,
            verbose=verbose,
            inheritable_tags=inheritable_tags,
            local_tags=local_tags,
            inheritable_metadata=inheritable_metadata,
            local_metadata=local_metadata,
            **extra,
        )

    CallbackManager.configure = classmethod(  # type: ignore[assignment]
        lambda _cls, *a, **kw: patched_configure(_cls, *a, **kw),  # pyright: ignore[reportCallIssue]
    )
    CallbackManager._adrian_cbm_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched CallbackManager.configure")


# --- 3. BaseChatModel ---


def _patch_chat_model() -> None:
    """Patch ``BaseChatModel.invoke`` / ``ainvoke`` / ``astream`` / ``stream``."""
    if getattr(BaseChatModel, "_adrian_chat_model_patched", False):
        return

    original_invoke = BaseChatModel.invoke
    original_ainvoke = BaseChatModel.ainvoke
    original_astream = BaseChatModel.astream
    original_stream = BaseChatModel.stream

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        return original_invoke(self, input, config=config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        return await original_ainvoke(self, input, config=config, **kwargs)

    async def patched_astream(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        async for chunk in original_astream(self, input, config=config, **kwargs):
            yield chunk

    def patched_stream(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        yield from original_stream(self, input, config=config, **kwargs)

    BaseChatModel.invoke = patched_invoke  # type: ignore[assignment]
    BaseChatModel.ainvoke = patched_ainvoke  # type: ignore[assignment]
    BaseChatModel.astream = patched_astream  # type: ignore[assignment]
    BaseChatModel.stream = patched_stream  # type: ignore[assignment]
    BaseChatModel._adrian_chat_model_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched BaseChatModel.invoke / ainvoke")


# --- 4. LangGraph Pregel ---


def _patch_langgraph() -> None:
    """Patch ``Pregel.invoke`` / ``ainvoke`` / ``astream``.

    The async patches also set the invocation_id ContextVar at the
    top-level call so all sub-agent events share the same ID.
    """
    try:
        from langgraph.pregel import Pregel
    except ImportError:
        return

    if getattr(Pregel, "_adrian_pregel_patched", False):
        return

    original_invoke = Pregel.invoke
    original_ainvoke = Pregel.ainvoke
    original_astream = Pregel.astream

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks into sync graph invocation."""
        config = _inject_callbacks(config)

        return original_invoke(self, input, config=config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks and set invocation_id.

        Only the top-level call sets the invocation_id. Nested calls
        (sub-agent ainvoke) inherit it via contextvars propagation.
        """
        config = _inject_callbacks(config)

        current = get_invocation_id()
        token = None

        if current is None:
            uuid_ = uuid4()
            token = set_invocation_id(str(uuid_))

        try:
            return await original_ainvoke(self, input, config=config, **kwargs)
        finally:
            if token is not None:
                token.var.reset(token)

    async def patched_astream(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks and set invocation_id for streaming."""
        config = _inject_callbacks(config)

        current = get_invocation_id()
        token = None

        if current is None:
            uuid_ = uuid4()
            token = set_invocation_id(str(uuid_))

        try:
            async for chunk in original_astream(self, input, config=config, **kwargs):
                yield chunk
        finally:
            if token is not None:
                token.var.reset(token)

    Pregel.invoke = patched_invoke  # type: ignore[assignment]
    Pregel.ainvoke = patched_ainvoke  # type: ignore[assignment]
    Pregel.astream = patched_astream  # type: ignore[assignment]
    Pregel._adrian_pregel_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched Pregel.invoke / ainvoke / astream")


# --- 5. ToolNode ---


def _extract_tool_calls(
    state: dict[str, Any] | list[BaseMessage] | Any,
) -> list[dict[str, Any]]:
    """Extract tool_calls from ToolNode input (all three dispatch shapes).

    Returns full tool_call dicts (with id, name, args) for backward
    compat with tests and callers that need the full shape.
    """
    # Shape 3: per-tool-call dict from _afunc dispatch
    if isinstance(state, dict) and "tool_call" in state:
        tc = state["tool_call"]
        if isinstance(tc, dict) and tc.get("id"):
            return [tc]
        tc_id = getattr(tc, "id", None)
        if tc_id:
            return [
                {
                    "id": tc_id,
                    "name": getattr(tc, "name", ""),
                    "args": getattr(tc, "args", {}),
                }
            ]
        return []

    # Shape 1/2: state dict or message list
    if isinstance(state, dict):
        messages = list(state.get("messages") or [])  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    elif isinstance(state, list):
        messages = list(state)
    else:
        return []

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            return msg.tool_calls  # type: ignore[no-any-return]

    return []


def _should_halt(verdict: pb.Verdict) -> bool:
    """Decide whether a verdict should halt tool execution.

    HITL resolutions override per-MAD policy when present.
    """
    if verdict.HasField("hitl"):
        return not verdict.hitl.continue_execution

    mad_prefix = verdict.mad_code[:2]
    return {
        "M0": verdict.policy.policy_m0,
        "M2": verdict.policy.policy_m2,
        "M3": verdict.policy.policy_m3,
        "M4": verdict.policy.policy_m4,
    }.get(mad_prefix, False)


def _patch_tool_node() -> None:
    """Patch ToolNode for callback injection + async verdict gate.

    ToolNode dispatches tools via tool.invoke (sync) even within async
    Pregel. BaseTool.invoke can't await a verdict from the event loop
    thread, so we add the verdict gate here on ToolNode.ainvoke — the
    entry point Pregel calls before tool dispatch begins. This is a
    complementary gate to BaseTool (which covers direct callers).
    """
    try:
        from langgraph.prebuilt import ToolNode
    except ImportError:
        return

    if getattr(ToolNode, "_adrian_tool_node_patched", False):
        return

    original_invoke = ToolNode.invoke
    original_ainvoke = ToolNode.ainvoke
    original_astream = getattr(ToolNode, "astream", None)

    async def _gate_tool_calls(state: Any) -> bool:  # noqa: ANN401
        """Returns True if tools should be BLOCKED."""
        ws = _ws_client
        if ws is None:
            return False
        if not ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
            try:
                await asyncio.wait_for(ws._login_ack_received.wait(), timeout=5.0)  # pyright: ignore[reportPrivateUsage]
            except TimeoutError:
                logger.warning("ToolNode: LoginAck not received within 5s; blocking")
                return True
        if not ws.policy_active():
            return False

        tc_ids: list[str] = [
            str(tc.get("id")) for tc in _extract_tool_calls(state) if tc.get("id")
        ]
        if not tc_ids:
            return False

        cfg = _get_config()
        timeout = ws.block_timeout(cfg.block_timeout if cfg else 30.0)
        verdict = await ws.wait_for_tool_call_verdict(tc_ids[0], timeout)
        if verdict is None:
            logger.warning("ToolNode: verdict timeout, blocking (fail-closed)")
            return True
        if _should_halt(verdict):
            logger.warning(
                "halting tool execution for event_id=%s mad_code=%s",
                verdict.event_id,
                verdict.mad_code,
            )
            return True
        return False

    def _build_blocked(state: Any) -> dict[str, list[ToolMessage]]:  # noqa: ANN401
        tc_ids = [tc.get("id") for tc in _extract_tool_calls(state) if tc.get("id")]
        return {
            "messages": [
                ToolMessage(
                    content="[BLOCKED by security policy]", tool_call_id=tid, name=""
                )
                for tid in tc_ids
            ]
        }

    def patched_invoke(
        self: Any,
        input: Any,
        config: Any = None,
        **kwargs: Any,  # noqa: A002, ANN401
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        return original_invoke(self, input, config=config, **kwargs)

    async def patched_ainvoke(
        self: Any,
        input: Any,
        config: Any = None,
        **kwargs: Any,  # noqa: A002, ANN401
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        # Verdict gate removed — BaseTool.ainvoke/arun is the single
        # gate layer. Gating here too caused double-gate: ToolNode
        # consumed the verdict future, BaseTool's gate registered a
        # fresh future that never resolved → 30s timeout on a benign
        # verdict. Callback injection is kept so events still flow.
        return await original_ainvoke(self, input, config=config, **kwargs)

    async def patched_astream(
        self: Any,
        input: Any,
        config: Any = None,
        **kwargs: Any,  # noqa: A002, ANN401
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        assert original_astream is not None  # guarded by line below
        async for chunk in original_astream(self, input, config=config, **kwargs):
            yield chunk

    ToolNode.invoke = patched_invoke  # type: ignore[assignment]
    ToolNode.ainvoke = patched_ainvoke  # type: ignore[assignment]
    if original_astream is not None:
        ToolNode.astream = patched_astream  # type: ignore[assignment]
    ToolNode._adrian_tool_node_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched ToolNode.invoke / ainvoke / astream")


# --- 6. BaseTool (universal verdict gate) ---


_BLOCKED_CONTENT = "[BLOCKED by security policy]"


def _patch_base_tool() -> None:
    """Patch ``BaseTool.invoke`` and ``BaseTool.ainvoke`` with the verdict gate.

    Every LangChain tool — whether dispatched by ToolNode, AgentExecutor,
    create_react_agent, or a manual ``tool.invoke(tool_call)`` loop —
    funnels through ``BaseTool.invoke`` (sync) or ``BaseTool.ainvoke``
    (async). Gating here covers all frameworks in one place.

    The gate extracts ``tool_call_id`` from the input (a ``ToolCall``
    TypedDict), awaits the classifier verdict for the producing LLM
    event, and returns a ``[BLOCKED]`` string instead of running the
    tool body when the verdict is in-scope (M3/M4 under MODE_BLOCK).

    In MODE_BLOCK, verdict timeout is fail-closed (block the tool)
    because the absence of a verdict in block mode is a policy violation.
    In MODE_ALERT, no gate fires at all (skip).
    """
    from langchain_core.tools import BaseTool
    from langchain_core.tools.base import (
        _is_tool_call,  # pyright: ignore[reportPrivateUsage]
    )

    if getattr(BaseTool, "_adrian_base_tool_patched", False):
        return

    original_invoke = BaseTool.invoke
    original_ainvoke = BaseTool.ainvoke

    def _extract_tool_call_id(input: Any) -> str | None:  # noqa: A002, ANN401
        """Extract tool_call_id from a ToolCall input, or None."""
        if isinstance(input, dict) and _is_tool_call(input):
            return input.get("id")
        return None

    async def _async_gate(tool_call_id: str) -> bool:
        """Returns True if the tool should be BLOCKED."""
        ws = _ws_client
        if ws is None:
            return False

        if not ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
            try:
                await asyncio.wait_for(
                    ws._login_ack_received.wait(),  # pyright: ignore[reportPrivateUsage]
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(
                    "BaseTool: LoginAck not received within 5s; "
                    "blocking tool (refusing to run without verified policy)"
                )
                return True

        if not ws.policy_active():
            return False

        cfg = _get_config()
        timeout = ws.block_timeout(cfg.block_timeout if cfg else 30.0)
        verdict = await ws.wait_for_tool_call_verdict(tool_call_id, timeout)

        if verdict is None:
            # Fail-closed in block mode: no verdict = block.
            logger.warning(
                "BaseTool: verdict timeout for tool_call_id=%s; "
                "blocking (fail-closed in MODE_BLOCK)",
                tool_call_id,
            )
            return True

        if _should_halt(verdict):
            logger.warning(
                "halting tool execution for event_id=%s mad_code=%s",
                verdict.event_id,
                verdict.mad_code,
            )
            return True

        return False

    def _sync_gate(tool_call_id: str) -> bool:
        """Sync verdict gate — works for pure-sync and worker-thread callers.

        Pure-sync (no event loop): runs ``_async_gate`` via
        ``loop.run_until_complete``.

        Worker-thread (Pregel dispatches sync tools on a thread-pool
        worker while the event loop runs on the main thread): bridges
        the async gate to the main loop via ``run_coroutine_threadsafe``
        and blocks the worker thread until the verdict resolves.

        Event-loop thread (calling tool.invoke directly from async
        code): cannot block — returns False (skip). The async path
        (BaseTool.ainvoke) handles this case.
        """
        ws = _ws_client
        if ws is None or not ws._login_ack_received.is_set() or not ws.policy_active():  # pyright: ignore[reportPrivateUsage]
            return False

        try:
            loop = asyncio.get_event_loop()
        except RuntimeError:
            return False

        if not loop.is_running():
            # Pure-sync caller — safe to block
            return loop.run_until_complete(_async_gate(tool_call_id))

        # Check if we're on a worker thread (no running loop on THIS
        # thread) vs the event-loop thread itself.
        try:
            asyncio.get_running_loop()
            # We ARE on the event-loop thread — can't block it.
            return False
        except RuntimeError:
            pass

        # Worker thread: bridge the async gate to the main loop.
        main_loop = getattr(ws, "_loop", None)
        if main_loop is None or not main_loop.is_running():
            return False

        try:
            cfg = _get_config()
            timeout = ws.block_timeout(cfg.block_timeout if cfg else 30.0)
            future = asyncio.run_coroutine_threadsafe(
                _async_gate(tool_call_id), main_loop
            )
            return future.result(timeout=timeout if timeout else 60.0)
        except Exception:
            return False

    def _blocked_response(tc_id: str) -> Any:  # noqa: ANN401
        """Return a blocked response compatible with ToolNode.

        Returns a ToolMessage for create_react_agent / ToolNode
        compatibility. Falls back to bare string on import failure.
        """
        try:
            return ToolMessage(content=_BLOCKED_CONTENT, tool_call_id=tc_id, name="")
        except Exception:
            return _BLOCKED_CONTENT

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        tc_id = _extract_tool_call_id(input)
        if tc_id and _sync_gate(tc_id):
            return _blocked_response(tc_id)
        return original_invoke(self, input, config=config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        config = _inject_callbacks(config)
        tc_id = _extract_tool_call_id(input)
        if tc_id and await _async_gate(tc_id):
            return _blocked_response(tc_id)
        return await original_ainvoke(self, input, config=config, **kwargs)

    original_arun = BaseTool.arun

    async def patched_arun(
        self: Any,  # noqa: ANN401
        tool_input: Any,  # noqa: ANN401
        *args: Any,
        tool_call_id: str | None = None,
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Gate on arun — AgentExecutor calls tool.arun directly."""
        if tool_call_id and await _async_gate(tool_call_id):
            return _blocked_response(tool_call_id)
        return await original_arun(
            self, tool_input, *args, tool_call_id=tool_call_id, **kwargs
        )

    BaseTool.invoke = patched_invoke  # type: ignore[assignment]
    BaseTool.ainvoke = patched_ainvoke  # type: ignore[assignment]
    BaseTool.arun = patched_arun  # type: ignore[assignment]
    BaseTool._adrian_base_tool_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched BaseTool.invoke / ainvoke / arun (universal verdict gate)")


# --- 7. AgentExecutor (tool_call_id on agent_action, not on tool.arun) ---


def _patch_agent_executor() -> None:
    """Patch AgentExecutor._aperform_agent_action for the executor path.

    AgentExecutor calls tool.arun without forwarding tool_call_id,
    so the BaseTool.arun gate can't extract it. The tool_call_id lives
    on agent_action.tool_call_id (set by OpenAI-style parsers). We
    intercept here, await the verdict, and return a blocked observation
    instead of calling the tool.
    """
    AgentExecutor = None
    AgentStep = None
    for mod_path in ("langchain_classic.agents.agent", "langchain.agents.agent"):
        try:
            mod = __import__(mod_path, fromlist=["AgentExecutor", "AgentStep"])
            AgentExecutor = getattr(mod, "AgentExecutor", None)
            AgentStep = getattr(mod, "AgentStep", None)
            if AgentExecutor and AgentStep:
                break
        except ImportError:
            continue

    if AgentExecutor is None or AgentStep is None:
        return
    if getattr(AgentExecutor, "_adrian_executor_patched", False):
        return

    original_aperform = AgentExecutor._aperform_agent_action

    async def patched_aperform(
        self: Any,
        name_to_tool_map: Any,
        color_mapping: Any,  # noqa: ANN401
        agent_action: Any,
        run_manager: Any = None,  # noqa: ANN401
    ) -> Any:  # noqa: ANN401
        tc_id = getattr(agent_action, "tool_call_id", None)
        if tc_id:
            ws = _ws_client
            if ws is not None:
                if not ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
                    try:
                        await asyncio.wait_for(
                            ws._login_ack_received.wait(),  # pyright: ignore[reportPrivateUsage]
                            timeout=5.0,
                        )
                    except TimeoutError:
                        logger.warning(
                            "AgentExecutor: LoginAck not received within 5s; blocking"
                        )
                        return AgentStep(
                            action=agent_action, observation=_BLOCKED_CONTENT
                        )
                if ws.policy_active():
                    cfg = _get_config()
                    timeout = ws.block_timeout(cfg.block_timeout if cfg else 30.0)
                    verdict = await ws.wait_for_tool_call_verdict(tc_id, timeout)
                    if verdict is None:
                        logger.warning(
                            "AgentExecutor: verdict timeout for tool_call_id=%s, blocking (fail-closed)",
                            tc_id,
                        )
                        return AgentStep(
                            action=agent_action, observation=_BLOCKED_CONTENT
                        )
                    if _should_halt(verdict):
                        logger.warning(
                            "halting tool execution for event_id=%s mad_code=%s",
                            verdict.event_id,
                            verdict.mad_code,
                        )
                        return AgentStep(
                            action=agent_action, observation=_BLOCKED_CONTENT
                        )
        return await original_aperform(
            self, name_to_tool_map, color_mapping, agent_action, run_manager
        )

    AgentExecutor._aperform_agent_action = patched_aperform  # type: ignore[assignment]
    AgentExecutor._adrian_executor_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched AgentExecutor._aperform_agent_action")
