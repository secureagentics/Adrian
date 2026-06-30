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
    # Default to a local self-hosted backend (the one `make dev` brings
    # up at deploy/compose.yaml). OSS users pointing at a remote
    # deployment override via ws_url= or ADRIAN_WS_URL.
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
            logger.warning(
                "Adrian initialised without a running event loop. WebSocket "
                "transport and BLOCK/HITL verdict handling may not be active "
                "yet; sync ToolNode.invoke will fail closed until an event "
                "loop connects the WebSocket and receives a policy LoginAck."
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
        logger.debug("LangChain auto-instrumentation applied")
    except ImportError:
        logger.debug("LangChain not found, skipping auto-instrumentation")
    except Exception:
        logger.exception("Auto-instrumentation failed")


# --- 1. Runnable ---


def _patch_runnable() -> None:
    """Patch ``Runnable.invoke`` / ``ainvoke`` to inject callbacks."""
    if getattr(Runnable, "_adrian_patched", False):
        return

    original_invoke = Runnable.invoke
    original_ainvoke = Runnable.ainvoke

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks into sync Runnable call."""
        config = _inject_callbacks(config)

        return original_invoke(self, input, config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks into async Runnable call."""
        config = _inject_callbacks(config)

        return await original_ainvoke(self, input, config, **kwargs)

    Runnable.invoke = patched_invoke  # type: ignore[assignment]
    Runnable.ainvoke = patched_ainvoke  # type: ignore[assignment]
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
    """Patch ``BaseChatModel.invoke`` / ``ainvoke`` to inject callbacks."""
    if getattr(BaseChatModel, "_adrian_chat_model_patched", False):
        return

    original_invoke = BaseChatModel.invoke
    original_ainvoke = BaseChatModel.ainvoke

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks into sync chat model call."""
        config = _inject_callbacks(config)

        return original_invoke(self, input, config=config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks into async chat model call."""
        config = _inject_callbacks(config)

        return await original_ainvoke(self, input, config=config, **kwargs)

    BaseChatModel.invoke = patched_invoke  # type: ignore[assignment]
    BaseChatModel.ainvoke = patched_ainvoke  # type: ignore[assignment]
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
    state: dict[str, Any] | list[BaseMessage],
) -> list[dict[str, Any]]:
    """Extract tool_calls from the ToolNode input.

    ``ToolNode`` is reached with three input shapes:
    1. a state dict whose ``"messages"`` key holds the message list
       (hand-built ``StateGraph`` with ``ToolNode`` as a node), or
    2. a bare list of messages, or
    3. a single per-tool-call dict ``{"__type", "tool_call", "state"}``
       — how langgraph-prebuilt / ``create_react_agent`` dispatch each
       tool call. The id lives at ``input["tool_call"]["id"]``.

    Shape 3 was previously unhandled: the function returned ``[]``, so the
    block/HITL gate never found a tool_call_id and ran the tool un-gated.

    Args:
        state: The ToolNode input (any of the three shapes above).

    Returns:
        List of tool call dicts, or an empty list when none is found.
    """
    # Shape 3: per-tool-call dispatch (create_react_agent / prebuilt ToolNode).
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

    if isinstance(state, dict):
        messages = list(state.get("messages") or [])  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
    else:
        messages = list(state)

    for msg in reversed(messages):
        if isinstance(msg, AIMessage) and getattr(msg, "tool_calls", None):
            return msg.tool_calls  # type: ignore[no-any-return]

    return []


def _should_halt(verdict: pb.Verdict) -> bool:
    """Decide whether a verdict should halt tool execution.

    HITL resolutions override everything: ``continue_execution=False``
    means halt, ``True`` means continue.  Otherwise the per-MAD policy
    bool is the sole scope authority, if the verdict's tier is
    in-scope, halt; if not, continue.
    """
    if verdict.HasField("hitl"):
        return not verdict.hitl.continue_execution

    mad_prefix = verdict.mad_code[:2]
    in_scope = {
        "M0": verdict.policy.policy_m0,
        "M2": verdict.policy.policy_m2,
        "M3": verdict.policy.policy_m3,
        "M4": verdict.policy.policy_m4,
    }.get(mad_prefix, False)

    return in_scope


def _build_blocked_response(
    tool_calls: list[dict[str, str]],
) -> dict[str, list[ToolMessage]]:
    """Build synthetic ToolMessage responses for blocked tool calls.

    Args:
        tool_calls: List of tool call dicts extracted from the AIMessage.

    Returns:
        Dict in the format ToolNode expects.
    """
    blocked_messages: list[ToolMessage] = [
        ToolMessage(
            content="[BLOCKED by security policy]",
            tool_call_id=str(tc.get("id", "")),
            name=str(tc.get("name", "")),
        )
        for tc in tool_calls
    ]

    return {"messages": blocked_messages}


def _resolved_tool_call_verdict(
    ws: WebSocketClient,
    tool_call_id: str,
) -> tuple[pb.Verdict | None, bool]:
    """Return an already-resolved verdict for ``tool_call_id`` if one exists."""
    event_id = ws._tool_call_id_to_event_id.get(tool_call_id)  # pyright: ignore[reportPrivateUsage]

    if event_id is None:
        return None, False

    fut = ws._pending_verdicts.get(event_id)  # pyright: ignore[reportPrivateUsage]

    if fut is None or not fut.done():
        return None, False

    try:
        return fut.result(), True
    except asyncio.CancelledError:
        logger.warning(
            "ToolNode: resolved sync verdict future was cancelled; halting "
            "tool_call_id=%s event_id=%s",
            tool_call_id,
            event_id,
        )
        return None, False
    except Exception:
        logger.exception(
            "ToolNode: resolved sync verdict future failed; halting "
            "tool_call_id=%s event_id=%s",
            tool_call_id,
            event_id,
        )
        return None, False
    finally:
        ws._pending_verdicts.pop(event_id, None)  # pyright: ignore[reportPrivateUsage]


def _sync_tool_node_policy_gate(input: Any) -> dict[str, list[ToolMessage]] | None:  # noqa: ANN401
    """Apply the BLOCK / HITL ToolNode gate from the sync invoke path.

    Returns a synthetic blocked response when execution should halt, or
    ``None`` when the original ToolNode should run.
    """
    ws = _ws_client

    if ws is None:
        return None

    tool_calls = _extract_tool_calls(input)

    if not ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
        logger.warning(
            "ToolNode: LoginAck not received in sync invoke; halting "
            "(refusing to run a tool without a verified policy)"
        )
        return _build_blocked_response(tool_calls)

    if not ws.policy_active():
        return None

    tool_call_id = next(
        (tc.get("id") for tc in tool_calls if tc.get("id")),
        None,
    )

    if not tool_call_id:
        return None

    verdict, resolved = _resolved_tool_call_verdict(ws, tool_call_id)

    if not resolved:
        logger.warning(
            "ToolNode: sync invoke cannot wait for a BLOCK/HITL verdict; "
            "halting tool_call_id=%s",
            tool_call_id,
        )
        return _build_blocked_response(tool_calls)

    if verdict is None:
        logger.warning(
            "ToolNode: sync invoke resolved an empty verdict; halting "
            "tool_call_id=%s",
            tool_call_id,
        )
        return _build_blocked_response(tool_calls)

    if _should_halt(verdict):
        logger.warning(
            "halting tool execution for event_id=%s mad_code=%s",
            verdict.event_id,
            verdict.mad_code,
        )
        return _build_blocked_response(tool_calls)

    return None


def _patch_tool_node() -> None:
    """Patch ``ToolNode.invoke`` / ``ainvoke``.

    The async path waits for the preceding LLM's verdict before executing
    tools. The sync path consumes already-resolved verdicts only; when
    policy/verdict state is unavailable it fails closed because the SDK
    cannot safely run the WebSocket wait without a running event loop.
    """
    try:
        from langgraph.prebuilt import ToolNode
    except ImportError:
        return

    if getattr(ToolNode, "_adrian_tool_node_patched", False):
        return

    original_invoke = ToolNode.invoke
    original_ainvoke = ToolNode.ainvoke

    def patched_invoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks; in BLOCK / HITL modes gate sync tools."""
        config = _inject_callbacks(config)
        blocked = _sync_tool_node_policy_gate(input)

        if blocked is not None:
            return blocked
        return original_invoke(self, input, config=config, **kwargs)

    async def patched_ainvoke(
        self: Any,  # noqa: ANN401
        input: Any,  # noqa: A002, ANN401
        config: Any = None,  # noqa: ANN401
        **kwargs: Any,
    ) -> Any:  # noqa: ANN401
        """Inject Adrian callbacks; in BLOCK / HITL modes wait for verdict.

        Per-tool-call correlation: every tool_call.id is mapped (in
        ``WebSocketClient`` ) to the event_id of the LLM that emitted
        it.  Each ToolNode invocation awaits its specific LLM's verdict,
        race-free under parallel agents, no graph-wide pause.
        """
        config = _inject_callbacks(config)
        ws = _ws_client

        if ws is None:
            return await original_ainvoke(self, input, config=config, **kwargs)

        # First-tool-call window: the recv loop may not have processed
        # ``LoginAck`` yet, so ``policy_active()`` reads False even
        # when the org is in BLOCK or HITL.  Wait for the LoginAck
        # event before checking.  If it doesn't arrive within the
        # window, halt, refusing to run is the only safe outcome
        # when we can't verify the org's policy.
        if not ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
            try:
                await asyncio.wait_for(
                    ws._login_ack_received.wait(),  # pyright: ignore[reportPrivateUsage]
                    timeout=5.0,
                )
            except TimeoutError:
                logger.warning(
                    "ToolNode: LoginAck not received within 5s; halting "
                    "(refusing to run a tool without a verified policy)"
                )
                return _build_blocked_response(_extract_tool_calls(input))

        if not ws.policy_active():
            return await original_ainvoke(self, input, config=config, **kwargs)

        tool_calls = _extract_tool_calls(input)
        tool_call_id = next(
            (tc.get("id") for tc in tool_calls if tc.get("id")),
            None,
        )

        if not tool_call_id:
            # Direct ToolNode invocation outside an LLM flow, no
            # producing event_id to wait on, so let the tool run.
            return await original_ainvoke(self, input, config=config, **kwargs)

        cfg = _get_config()
        timeout = ws.block_timeout(cfg.block_timeout if cfg else 30.0)

        verdict = await ws.wait_for_tool_call_verdict(tool_call_id, timeout)

        if verdict is None:
            logger.warning(
                "verdict timeout for tool_call_id=%s, fail-open",
                tool_call_id,
            )
            return await original_ainvoke(self, input, config=config, **kwargs)

        if _should_halt(verdict):
            logger.warning(
                "halting tool execution for event_id=%s mad_code=%s",
                verdict.event_id,
                verdict.mad_code,
            )
            return _build_blocked_response(tool_calls)

        return await original_ainvoke(self, input, config=config, **kwargs)

    ToolNode.invoke = patched_invoke  # type: ignore[assignment]
    ToolNode.ainvoke = patched_ainvoke  # type: ignore[assignment]
    ToolNode._adrian_tool_node_patched = True  # type: ignore[attr-defined]
    logger.debug("Patched ToolNode.invoke / ainvoke")
