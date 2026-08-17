# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
#
# Licensed under the Apache Licence, Version 2.0 (the "Licence").
# You may not use this file except in compliance with the Licence.
# A copy of the Licence is included at LICENSE in the repository root.
"""LangChain / LangGraph instrumentation for Adrian.

Monkey-patches ``Runnable``, ``CallbackManager``, ``BaseChatModel``, LangGraph
``Pregel`` / ``ToolNode``, ``BaseTool`` (the universal verdict gate) and
``AgentExecutor`` so that all LLM and tool activity is captured, paired, and
emitted, and - under MODE_BLOCK / MODE_HITL - gated on the classifier verdict
before the tool body runs.  Every patch is idempotent; calling
:func:`patch_langchain` again after a shutdown / re-init only updates the
internal getters, it does not re-wrap an already-patched method.

The handler reads Adrian state (the callback handler, the WebSocket client and
the config) through three getters injected by :func:`patch_langchain` and read
at call time, so shutdown + re-init are honoured without importing the
``adrian`` package back into this module.
"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from langchain_core.callbacks.manager import CallbackManager
from langchain_core.language_models.chat_models import BaseChatModel
from langchain_core.messages import AIMessage, BaseMessage, ToolMessage
from langchain_core.runnables.base import Runnable
from langchain_core.runnables.config import ensure_config

from adrian.context import get_invocation_id, set_invocation_id
from adrian.ws import should_halt

if TYPE_CHECKING:
    from adrian.config import AdrianConfig
    from adrian.handler import AdrianCallbackHandler
    from adrian.ws import WebSocketClient

logger = logging.getLogger("adrian")


def _getter_unset() -> None:
    """Return None until :func:`patch_langchain` injects the real getter."""
    return None


# Set by patch_langchain(); read at call time so shutdown + re-init works.
_handler_getter: Callable[[], AdrianCallbackHandler | None] = _getter_unset
_ws_getter: Callable[[], WebSocketClient | None] = _getter_unset
_config_getter: Callable[[], AdrianConfig | None] = _getter_unset


def patch_langchain(
    handler_getter: Callable[[], AdrianCallbackHandler | None],
    ws_getter: Callable[[], WebSocketClient | None],
    config_getter: Callable[[], AdrianConfig | None],
) -> None:
    """Install the LangChain / LangGraph monkey-patches.

    Stores the three state getters (read at call time so shutdown and re-init
    are honoured) and then applies every patch idempotently.  Invoked by the
    thin ``adrian.patch_langchain()`` wrapper, which injects closures over the
    package globals.
    """
    global _handler_getter, _ws_getter, _config_getter  # noqa: PLW0603
    _handler_getter = handler_getter
    _ws_getter = ws_getter
    _config_getter = config_getter

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


def _inject_callbacks(config: Any) -> Any:  # noqa: ANN401
    """Merge the Adrian handler into a LangChain ``RunnableConfig``.

    Args:
        config: An existing LangChain RunnableConfig or ``None``.

    Returns:
        A config dict guaranteed to contain the Adrian handler.
    """
    handler = _handler_getter()

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
        handler = _handler_getter()

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


def _extract_tool_calls(  # pyright: ignore[reportUnusedFunction]
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


def _patch_tool_node() -> None:
    """Patch ToolNode for callback injection + async verdict gate.

    ToolNode dispatches tools via tool.invoke (sync) even within async
    Pregel. BaseTool.invoke can't await a verdict from the event loop
    thread, so we add the verdict gate here on ToolNode.ainvoke - the
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
        # Verdict gate removed - BaseTool.ainvoke/arun is the single
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

    Every LangChain tool - whether dispatched by ToolNode, AgentExecutor,
    create_react_agent, or a manual ``tool.invoke(tool_call)`` loop -
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
        ws = _ws_getter()
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

        cfg = _config_getter()
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

        if should_halt(verdict):
            logger.warning(
                "halting tool execution for event_id=%s mad_code=%s",
                verdict.event_id,
                verdict.mad_code,
            )
            return True

        return False

    def _sync_gate(tool_call_id: str) -> bool:
        """Sync verdict gate - works for pure-sync and worker-thread callers.

        Worker-thread (the common LangGraph case: ``StructuredTool.ainvoke``
        dispatches a *sync* tool via ``run_in_executor(self.invoke)``, so the
        gate runs on a thread-pool worker while the WS event loop runs on
        another thread): bridges the async gate onto the WS loop via
        ``run_coroutine_threadsafe`` and blocks the worker until the verdict
        resolves.

        Pure-sync (no event loop anywhere): runs ``_async_gate`` to
        completion on this thread.

        Event-loop thread (calling ``tool.invoke`` directly from async
        code): cannot block without deadlocking - returns False (skip).
        The async path (``BaseTool.ainvoke``) handles this case.

        Thread detection uses ``asyncio.get_running_loop()`` rather than
        ``get_event_loop()``: the latter raises ``RuntimeError`` on a worker
        thread (no loop *set* there, since Python 3.10+), which would
        misclassify the worker-thread case as "no loop" and skip the gate -
        leaving sync tools ungated under ``create_react_agent``.
        """
        ws = _ws_getter()
        if ws is None or not ws._login_ack_received.is_set() or not ws.policy_active():  # pyright: ignore[reportPrivateUsage]
            return False

        # Is THIS thread running an event loop?
        try:
            asyncio.get_running_loop()
        except RuntimeError:
            pass  # no loop on this thread: worker thread or pure-sync caller
        else:
            # On the event-loop thread - can't block it. The async gate
            # (BaseTool.ainvoke) covers direct-from-async callers.
            return False

        # Worker thread: the WS loop runs elsewhere - bridge onto it and
        # block this worker until the verdict resolves. ``_async_gate`` owns
        # the wait policy (bounded with fail-closed in MODE_BLOCK, indefinite
        # in MODE_HITL where execution must pause until a human acts), so we
        # wait on the future with no timeout of our own - a finite timeout
        # here would fail-open a HITL hold once it elapsed. Fail closed (treat
        # as halt) if the bridge itself raises.
        main_loop = getattr(ws, "_loop", None)
        if main_loop is not None and main_loop.is_running():
            try:
                future = asyncio.run_coroutine_threadsafe(
                    _async_gate(tool_call_id), main_loop
                )
                return future.result()
            except Exception:
                return True

        # Pure-sync caller, no loop anywhere - run the gate to completion.
        try:
            return asyncio.run(_async_gate(tool_call_id))
        except Exception:
            return True

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
        """Gate on arun - AgentExecutor calls tool.arun directly."""
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
            ws = _ws_getter()
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
                    cfg = _config_getter()
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
                    if should_halt(verdict):
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
