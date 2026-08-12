# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
#
# Licensed under the Apache Licence, Version 2.0 (the "Licence").
# You may not use this file except in compliance with the Licence.
# A copy of the Licence is included at LICENSE in the repository root.
"""Anthropic SDK instrumentation for Adrian.

Patches ``anthropic.Anthropic`` and ``anthropic.AsyncAnthropic`` so that every
``messages.create`` and ``messages.stream`` call is captured as an Adrian
``PairedEvent`` and emitted through the hook registry.  The patch is idempotent;
calling :func:`patch_anthropic` again after a shutdown / re-init only updates the
internal getters, it does not re-wrap the already-patched methods.

Usage without auto-instrumentation::

    import anthropic
    import adrian

    adrian.init(api_key="...", auto_instrument=False)
    adrian.patch_anthropic()

    client = anthropic.AsyncAnthropic()
    response = await client.messages.create(model="...", ...)

To group multi-turn calls under a single invocation ID::

    async with adrian.anthropic_invocation():
        r1 = await client.messages.create(...)
        r2 = await client.messages.create(...)  # same invocation_id as r1

Without that wrapper each call is emitted with ``invocation_id="no_invocation"``
and an INFO log line, because the Anthropic SDK -- unlike LangGraph, where
``Pregel.ainvoke`` bounds a unit of work -- exposes only point requests with no
boundary to scope an invocation to.

Streaming
---------

``client.messages.stream(...)`` returns a context manager rather than a
response, so the returned manager is wrapped and the stream's terminal methods
are rerouted through the same emit + gate path as ``create``::

    with client.messages.stream(model="...", ...) as stream:
        for text in stream.text_stream:
            print(text, end="")

        message = stream.get_final_message()   # emitted and gated here

Under ``MODE_BLOCK`` / ``MODE_HITL`` a halted ``tool_use`` block in the final
message is rewritten to a ``[BLOCKED]`` text block exactly as in the
non-streaming path.  Known limits:

* Gating happens at ``get_final_message()`` / ``until_done()`` (and so also at
  ``get_final_text()``, which delegates to the former).  A consumer that acts on
  raw ``content_block_stop`` events instead is emitted for audit (on
  context-manager exit) but not gated -- raw event iteration is planned for a
  follow-up change.
* A ``stream()`` call used outside a ``with`` block is not instrumented at all;
  ``__enter__`` is the seam.
"""

# pyright: reportUnknownVariableType=false
# pyright: reportUnknownMemberType=false
# pyright: reportUnknownArgumentType=false
# pyright: reportUnknownLambdaType=false

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from contextlib import asynccontextmanager, contextmanager
from datetime import UTC, datetime
from types import SimpleNamespace
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from adrian.config import AdrianConfig
from adrian.context import get_invocation_id, in_chat_model, set_invocation_id
from adrian.format.types import AgentContext, LlmPairData, PairedEvent
from adrian.hooks import HookRegistry
from adrian.types import ChatMessage, EventData, TokenUsage, ToolCallRecord
from adrian.ws import should_halt

if TYPE_CHECKING:
    from contextvars import Token

    from adrian.handler import AdrianCallbackHandler
    from adrian.ws import WebSocketClient

logger = logging.getLogger("adrian.anthropic")

# Content substituted for a tool_use block whose verdict says halt.  A text
# block (rather than a raised error) keeps the response usable and lets the
# model see that its action was stopped, mirroring the LangChain gate.
_BLOCKED_CONTENT = "[BLOCKED by security policy]"

# Set once by patch_anthropic(); read at call time so shutdown + re-init works.
_hooks_getter: Callable[[], HookRegistry | None] | None = None
_config_getter: Callable[[], AdrianConfig | None] | None = None
_ws_getter: Callable[[], WebSocketClient | None] | None = None
_handler_getter: Callable[[], AdrianCallbackHandler | None] | None = None


# ------------------------------------------------------------------
# Message format conversion
# ------------------------------------------------------------------


def _flatten_content(content: Any) -> str:  # noqa: ANN401
    """Flatten Anthropic message content to a plain string.

    Anthropic messages carry either a plain string or a list of content
    blocks (``TextBlockParam``, ``ToolUseBlockParam``, ``ToolResultBlockParam``,
    and so on).  Both forms are normalised to a plain string for
    ``ChatMessage.content``.

    Args:
        content: Anthropic message content -- a string or a block list.

    Returns:
        Plain string representation.
    """
    if isinstance(content, str):
        return content

    if not isinstance(content, list):
        return str(content)

    parts: list[str] = []

    for block in content:
        if hasattr(block, "type"):
            # SDK typed objects (TextBlock, ToolUseBlock, ToolResultBlock, …)
            btype = block.type

            if btype == "text":
                parts.append(getattr(block, "text", ""))
            elif btype == "tool_use":
                name = getattr(block, "name", "unknown")
                args = getattr(block, "input", {})
                parts.append(f"[tool_use: {name} args={args}]")
            elif btype == "tool_result":
                inner = getattr(block, "content", "")
                parts.append(_flatten_content(inner))
        elif isinstance(block, dict):
            btype = block.get("type", "")

            if btype == "text":
                parts.append(str(block.get("text", "")))
            elif btype == "tool_use":
                name = block.get("name", "unknown")
                args = block.get("input", {})
                parts.append(f"[tool_use: {name} args={args}]")
            elif btype == "tool_result":
                inner = block.get("content", "")
                parts.append(_flatten_content(inner))

    return "\n".join(p for p in parts if p)


def _flatten_anthropic_messages(
    messages: list[dict[str, Any]],
    system: str | list[Any] | None,
) -> list[ChatMessage]:
    """Convert Anthropic message params to a flat ``ChatMessage`` list.

    Prepends the system prompt (if any) as a ``"system"`` role entry,
    then converts each user / assistant turn in order.

    Args:
        messages: Anthropic ``messages`` parameter -- a list of dicts with
            ``role`` and ``content`` keys.
        system: Anthropic ``system`` parameter -- a string, a block list, or
            ``None``.

    Returns:
        Flat list of ``ChatMessage`` dicts compatible with the Adrian format.
    """
    result: list[ChatMessage] = []

    if system is not None:
        result.append(ChatMessage(role="system", content=_flatten_content(system)))

    for msg in messages:
        role = str(msg.get("role", "unknown"))
        content = _flatten_content(msg.get("content", ""))
        result.append(ChatMessage(role=role, content=content))

    return result


def _extract_anthropic_tool_calls(content: list[Any]) -> list[ToolCallRecord]:
    """Extract tool call records from an Anthropic response content list.

    Scans for ``ToolUseBlock`` SDK objects or ``tool_use`` dicts and converts
    each to a ``ToolCallRecord``.

    Args:
        content: ``Message.content`` from the Anthropic response.

    Returns:
        List of ``ToolCallRecord`` dicts, empty when no tool calls are present.
    """
    records: list[ToolCallRecord] = []

    for block in content:
        if hasattr(block, "type") and block.type == "tool_use":
            args = getattr(block, "input", {})

            if not isinstance(args, dict):
                try:
                    args = dict(args)
                except (TypeError, ValueError):
                    args = {}

            records.append(
                ToolCallRecord(
                    id=str(getattr(block, "id", "")),
                    name=str(getattr(block, "name", "unknown")),
                    args=args,
                )
            )
        elif isinstance(block, dict) and block.get("type") == "tool_use":
            args = block.get("input", {})

            if not isinstance(args, dict):
                args = {}

            records.append(
                ToolCallRecord(
                    id=str(block.get("id", "")),
                    name=str(block.get("name", "unknown")),
                    args=args,
                )
            )

    return records


def _extract_anthropic_usage(response: Any) -> TokenUsage | None:
    """Extract token usage from an Anthropic ``Message`` response object.

    Args:
        response: ``anthropic.types.Message`` or any object with a ``usage``
            attribute carrying ``input_tokens`` and ``output_tokens``.

    Returns:
        ``TokenUsage`` TypedDict, or ``None`` if usage data is absent.
    """
    usage = getattr(response, "usage", None)

    if usage is None:
        return None

    input_tokens: int = getattr(usage, "input_tokens", 0) or 0
    output_tokens: int = getattr(usage, "output_tokens", 0) or 0

    return TokenUsage(
        prompt_tokens=input_tokens,
        completion_tokens=output_tokens,
        total_tokens=input_tokens + output_tokens,
    )


def _extract_response_text(content: list[Any]) -> str:
    """Extract plain text output from an Anthropic response content list.

    Args:
        content: ``Message.content`` from the Anthropic response.

    Returns:
        Concatenated text from all ``TextBlock`` entries, joined by newlines.
    """
    parts: list[str] = []

    for block in content:
        if hasattr(block, "type") and block.type == "text":
            parts.append(getattr(block, "text", ""))
        elif isinstance(block, dict) and block.get("type") == "text":
            parts.append(str(block.get("text", "")))

    return "\n".join(p for p in parts if p)


def _request_summarised_thinking(kwargs: dict[str, Any]) -> None:
    """Ask for summarised thinking when the caller enabled it but left it hidden.

    ``thinking.display`` defaults to ``"omitted"`` on current models, so
    thinking blocks arrive with empty text and there is nothing to capture.
    ``display`` controls visibility only: thinking still runs and bills the
    same either way, so opting in costs nothing.  Callers who set ``display``
    themselves are left alone, as are callers who never enabled thinking.

    Args:
        kwargs: ``messages.create`` keyword arguments, mutated in place.
    """
    thinking = kwargs.get("thinking")

    if not isinstance(thinking, dict):
        return

    if thinking.get("type") not in ("adaptive", "enabled"):
        return

    if "display" in thinking:
        return

    kwargs["thinking"] = {**thinking, "display": "summarized"}


def _extract_reasoning(content: list[Any]) -> str:
    """Extract the model's reasoning from an Anthropic response content list.

    Args:
        content: ``Message.content`` from the Anthropic response.

    Returns:
        Concatenated text from all ``ThinkingBlock`` entries.  Empty when the
        model produced none, or when ``thinking.display`` left them redacted.
    """
    parts: list[str] = []

    for block in content:
        if hasattr(block, "type") and block.type == "thinking":
            parts.append(getattr(block, "thinking", ""))
        elif isinstance(block, dict) and block.get("type") == "thinking":
            parts.append(str(block.get("thinking", "")))

    return "\n\n".join(p for p in parts if p)


def _derive_agent_id(messages: list[ChatMessage]) -> str:
    """Derive a stable agent identity from the system prompt.

    Without LangGraph checkpoint metadata, the system prompt is the primary
    signal for agent identity.  Returns ``"default"`` when no system message
    is present.

    Args:
        messages: Flattened message list, may contain a ``"system"`` entry.

    Returns:
        Agent identifier string (at most 64 characters).
    """
    for msg in messages:
        if msg.get("role") == "system":
            content = msg["content"].strip()

            if content:
                return content[:64].replace("\n", " ")

    return "default"


# ------------------------------------------------------------------
# PairedEvent assembly
# ------------------------------------------------------------------


def build_anthropic_llm_pair(
    *,
    flat_messages: list[ChatMessage],
    response: Any,
    model: str,
    session_id: str,
    invocation_id: str,
    run_id: str,
) -> PairedEvent:
    """Assemble a ``PairedEvent`` from an Anthropic ``messages.create`` call.

    Args:
        flat_messages: Converted input messages (includes system prompt at index 0
            when present).
        response: Raw ``anthropic.types.Message`` response object.
        model: Model identifier from the request parameters.
        session_id: Adrian session identifier.
        invocation_id: Invocation correlation ID.
        run_id: Per-call unique identifier generated by the patch.

    Returns:
        Assembled ``PairedEvent`` with ``pair_type="llm"``.
    """
    system_prompt = ""
    user_instruction = ""

    for msg in flat_messages:
        if msg.get("role") == "system" and not system_prompt:
            system_prompt = msg["content"]

    for msg in reversed(flat_messages):
        if msg.get("role") == "user":
            user_instruction = msg["content"]
            break

    content: list[Any] = getattr(response, "content", [])
    output_text = _extract_response_text(content)
    reasoning = _extract_reasoning(content)
    tool_calls = _extract_anthropic_tool_calls(content)
    usage = _extract_anthropic_usage(response)

    # Prefer the model identifier echoed by the server; fall back to the request param.
    response_model: str = getattr(response, "model", "") or model

    return PairedEvent(
        event_id=str(uuid4()),
        invocation_id=invocation_id,
        session_id=session_id,
        run_id=run_id,
        timestamp=datetime.now(UTC).isoformat(),
        pair_type="llm",
        agent=AgentContext(
            agent_id=_derive_agent_id(flat_messages),
            system_prompt=system_prompt,
            user_instruction=user_instruction,
        ),
        parent=None,
        data=LlmPairData(
            model=response_model,
            messages=flat_messages,
            output=output_text,
            tool_calls=tool_calls,
            usage=usage,
            reasoning=reasoning,
        ),
    )


# ------------------------------------------------------------------
# Emission helpers
# ------------------------------------------------------------------


def _resolve_invocation_id(captured: str | None = None) -> str:
    """Resolve the invocation ID for an emitted event.

    Unlike LangGraph -- where ``Pregel.ainvoke`` is a natural unit of work the
    patch can scope an invocation to -- the Anthropic SDK exposes only point
    requests, so an unwrapped call genuinely has no invocation to belong to.
    Mirrors ``AdrianCallbackHandler._resolve_invocation_id``: log and fall back
    to the sentinel rather than inventing an ID.

    Args:
        captured: Invocation ID sampled earlier, used by the streaming path
            where the context variable may be out of scope by the time the
            stream is consumed.  ``None`` falls back to the current context.

    Returns:
        The resolved invocation ID, or ``"no_invocation"``.
    """
    invocation_id = captured or get_invocation_id()

    if invocation_id is None:
        # The event is still emitted; it just cannot be correlated with the
        # other calls in the same logical task.  INFO not WARN: nothing is
        # dropped.
        logger.info(
            "Anthropic call made outside of an invocation context; event will "
            "be emitted with invocation_id=no_invocation.  Wrap related calls "
            "in adrian.anthropic_invocation() to correlate them."
        )
        return "no_invocation"

    return invocation_id


async def _emit_pair(
    response: Any,  # noqa: ANN401
    kwargs: dict[str, Any],
    *,
    invocation_id: str | None = None,
) -> None:
    """Assemble and emit a ``PairedEvent`` for a completed ``messages.create`` call.

    Reads hooks / config / handler at call time so the correct state is used
    even if :func:`~adrian.shutdown` and :func:`~adrian.init` have been called
    since the patch was applied.

    When the callback handler is available, emission is delegated to it so the
    event is registered in the handler's event map -- this is what lets the
    developer ``on_verdict`` / ``on_block`` / ``on_audit`` callbacks fire for
    Anthropic calls (they are keyed on that map), matching the LangChain
    integration.  Without a handler (e.g. ``auto_instrument=False`` before
    ``init``) it falls back to emitting straight through the hook registry.

    Args:
        response: Anthropic ``Message`` response object.
        kwargs: Original ``messages.create`` keyword arguments.
        invocation_id: Invocation ID sampled at call time; see
            :func:`_resolve_invocation_id`.  ``None`` reads the current context.
    """
    if _hooks_getter is None or _config_getter is None:
        return

    hooks = _hooks_getter()
    config = _config_getter()

    if hooks is None or config is None:
        return

    handler = _handler_getter() if _handler_getter is not None else None

    try:
        session_id = config.session_id
        messages_param: list[dict[str, Any]] = list(kwargs.get("messages") or [])
        system_param: str | list[Any] | None = kwargs.get("system")
        model_param: str = str(kwargs.get("model", "unknown"))

        flat_messages = _flatten_anthropic_messages(messages_param, system_param)
        resolved_invocation_id = _resolve_invocation_id(invocation_id)
        run_id = str(uuid4())

        pair = build_anthropic_llm_pair(
            flat_messages=flat_messages,
            response=response,
            model=model_param,
            session_id=session_id,
            invocation_id=resolved_invocation_id,
            run_id=run_id,
        )

        if handler is not None:
            # Emits through the same hooks, registers the event for verdict
            # enrichment, and fires on_event -- so the notification callbacks
            # reach the developer for Anthropic events too.
            await handler._emit_pair(pair)  # pyright: ignore[reportPrivateUsage]
            return

        await hooks.emit(pair)

        if config.on_event is not None:
            from typing import cast

            result = config.on_event(
                pair.pair_type,
                cast(EventData, pair.data),
                pair.run_id,
                None,
                pair.event_id,
            )

            if asyncio.iscoroutine(result):
                await result

    except Exception:
        logger.exception("Failed to emit Anthropic paired event")


def _schedule_emit(
    response: Any,  # noqa: ANN401
    kwargs: dict[str, Any],
    *,
    invocation_id: str | None = None,
) -> None:
    """Schedule event emission from a synchronous call site.

    When inside a running event loop, schedules a fire-and-forget task so
    the sync caller is not blocked.  When no loop is running, blocks until
    emission completes so the event is not silently dropped.

    Args:
        response: Anthropic ``Message`` response object.
        kwargs: Original ``messages.create`` keyword arguments.
        invocation_id: Invocation ID sampled at call time, or ``None``.
    """
    coro = _emit_pair(response, kwargs, invocation_id=invocation_id)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception:
            logger.exception("Failed to emit Anthropic event (sync path)")


# ------------------------------------------------------------------
# Verdict gate (MODE_BLOCK / MODE_HITL)
# ------------------------------------------------------------------


def _blocked_text_block(original_block: Any) -> Any:  # noqa: ANN401
    """Build a text block used to replace a blocked ``tool_use`` block.

    Matches the shape of ``original_block`` so the rewritten response stays
    consistent: a dict block is replaced with a dict, an SDK block with a real
    ``anthropic.types.TextBlock`` when the package is importable, falling back
    to a lightweight attribute shim otherwise (e.g. under test).

    Args:
        original_block: The ``tool_use`` block being replaced.

    Returns:
        A ``"text"`` block carrying :data:`_BLOCKED_CONTENT`.
    """
    if isinstance(original_block, dict):
        return {"type": "text", "text": _BLOCKED_CONTENT}

    try:
        from anthropic.types import TextBlock

        return TextBlock(type="text", text=_BLOCKED_CONTENT)
    except Exception:  # noqa: BLE001 - anthropic missing or signature drift
        return SimpleNamespace(type="text", text=_BLOCKED_CONTENT)


def _rewrite_blocked_response(response: Any, blocked_ids: set[str]) -> Any:  # noqa: ANN401
    """Replace blocked ``tool_use`` blocks in a response with ``[BLOCKED]`` text.

    Rebuilds ``response.content`` in place, swapping every ``tool_use`` block
    whose id is in ``blocked_ids`` for a text block.  When no ``tool_use`` block
    survives, ``stop_reason`` is downgraded from ``"tool_use"`` to ``"end_turn"``
    so the caller's agent loop terminates cleanly instead of expecting a tool
    result.

    Args:
        response: Anthropic ``Message`` response object.
        blocked_ids: ``tool_use`` block ids to replace.

    Returns:
        The same ``response`` object, mutated.
    """
    content: list[Any] = getattr(response, "content", [])
    new_content: list[Any] = []
    tool_use_remains = False

    for block in content:
        if isinstance(block, dict):
            btype = block.get("type")
            bid = str(block.get("id", ""))
        else:
            btype = getattr(block, "type", None)
            bid = str(getattr(block, "id", ""))

        if btype == "tool_use" and bid in blocked_ids:
            new_content.append(_blocked_text_block(block))
        else:
            new_content.append(block)
            if btype == "tool_use":
                tool_use_remains = True

    response.content = new_content

    if not tool_use_remains and getattr(response, "stop_reason", None) == "tool_use":
        response.stop_reason = "end_turn"

    return response


async def _gate_response(response: Any, _kwargs: dict[str, Any]) -> Any:  # noqa: ANN401
    """Hold a response on the classifier verdict before returning it.

    Runs after :func:`_emit_pair` (which registers the pending verdict future
    for the producing LLM event).  In ``MODE_ALERT`` / unset state the response
    passes through unchanged.  In ``MODE_BLOCK`` / ``MODE_HITL`` each
    ``tool_use`` block waits for its verdict; blocked blocks are rewritten to a
    ``[BLOCKED]`` text block.

    Fail-closed rules match the LangChain gate: a missing ``LoginAck`` within 5s
    blocks all tool calls, and in ``MODE_BLOCK`` a verdict timeout blocks the
    tool call (absence of a verdict is a policy violation).  ``MODE_HITL`` waits
    indefinitely.

    Args:
        response: Anthropic ``Message`` response object.
        _kwargs: Original ``messages.create`` keyword arguments (unused; kept for
            symmetry with :func:`_emit_pair`).

    Returns:
        The response, rewritten in place when any tool call was blocked.
    """
    if _ws_getter is None:
        return response

    ws = _ws_getter()

    if ws is None:
        return response

    content: Any = getattr(response, "content", None)

    if not isinstance(content, list):
        return response

    tool_ids = [
        rec["id"] for rec in _extract_anthropic_tool_calls(content) if rec["id"]
    ]

    if not tool_ids:
        return response

    # Refuse to run without a verified policy: block if LoginAck is late.
    if not ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
        try:
            await asyncio.wait_for(
                ws._login_ack_received.wait(),  # pyright: ignore[reportPrivateUsage]
                timeout=5.0,
            )
        except TimeoutError:
            logger.warning(
                "Anthropic gate: LoginAck not received within 5s; "
                "blocking all tool calls (refusing to run without policy)"
            )
            return _rewrite_blocked_response(response, set(tool_ids))

    # ALERT / unset: observe only, no gating.
    if not ws.policy_active():
        return response

    config = _config_getter() if _config_getter is not None else None
    timeout = ws.block_timeout(config.block_timeout if config else 30.0)

    blocked: set[str] = set()

    for tc_id in tool_ids:
        verdict = await ws.wait_for_tool_call_verdict(tc_id, timeout)

        if verdict is None:
            # Fail-closed in block mode: no verdict = block.
            logger.warning(
                "Anthropic gate: verdict timeout for tool_call_id=%s; "
                "blocking (fail-closed in MODE_BLOCK)",
                tc_id,
            )
            blocked.add(tc_id)
        elif should_halt(verdict):
            logger.warning(
                "Anthropic gate: halting tool_call_id=%s event_id=%s mad_code=%s",
                tc_id,
                verdict.event_id,
                verdict.mad_code,
            )
            blocked.add(tc_id)

    if blocked:
        return _rewrite_blocked_response(response, blocked)

    return response


def _emit_and_gate_sync(
    response: Any,  # noqa: ANN401
    kwargs: dict[str, Any],
    *,
    invocation_id: str | None = None,
) -> Any:  # noqa: ANN401
    """Emit and (under BLOCK/HITL) gate a response from a synchronous call site.

    The verdict futures live on the WebSocket client's event loop, so emission
    and gating must run there together: emitting on a different loop would
    register the wait future where the verdict frame never resolves it.  When a
    WS loop is running on another thread both steps are bridged onto it via
    ``run_coroutine_threadsafe``; otherwise they run to completion here via
    ``asyncio.run``.  Either way the caller blocks until the (possibly
    rewritten) response comes back.  This mirrors the LangChain ``_sync_gate``.

    Once gating is engaged (see :func:`_should_gate_sync`) every exit fails
    **closed**: a bridge/gate error blocks the response's tool calls rather than
    letting them through, matching ``langchain_handler._sync_gate``.  Only when
    gating is not engaged at all -- no WS client, ALERT / post-login inactive
    policy, or a call from an event-loop thread that must not be blocked -- does
    it degrade to audit-only emission and return the response unchanged.

    Args:
        response: Anthropic ``Message`` response object.
        kwargs: Original ``messages.create`` keyword arguments.
        invocation_id: Invocation ID sampled at call time, or ``None``.

    Returns:
        The response, rewritten in place when a tool call was (or must be,
        on error) blocked.
    """
    ws = _ws_getter() if _ws_getter is not None else None

    # Not gating: no backend, inactive policy, or an event-loop thread we must
    # not block -- emit for audit and pass the response through unchanged.
    if ws is None or not _should_gate_sync(ws):
        _schedule_emit(response, kwargs, invocation_id=invocation_id)
        return response

    # Gating is engaged: every path below must fail closed.
    async def _emit_then_gate() -> Any:  # noqa: ANN401
        await _emit_pair(response, kwargs, invocation_id=invocation_id)
        return await _gate_response(response, kwargs)

    main_loop = getattr(ws, "_loop", None)

    try:
        if main_loop is not None and main_loop.is_running():
            # WS loop runs on another thread: bridge onto it and block here.
            return asyncio.run_coroutine_threadsafe(
                _emit_then_gate(), main_loop
            ).result()

        # No WS loop on another thread: run to completion on a temporary loop.
        # With no live connection no verdict arrives, so the gate fail-closes
        # via timeout -- the same outcome as LangChain's asyncio.run fallback.
        return asyncio.run(_emit_then_gate())
    except Exception:
        # Fail closed: a bridge/gate failure must not let a halted tool call
        # through.  Block every tool call in the response.
        logger.exception(
            "Anthropic sync gate failed; failing closed (blocking tool calls)"
        )
        blocked = {
            rec["id"]
            for rec in _extract_anthropic_tool_calls(
                getattr(response, "content", []) or []
            )
            if rec["id"]
        }
        return _rewrite_blocked_response(response, blocked)


def _should_gate_sync(ws: WebSocketClient) -> bool:
    """Whether the sync path should gate rather than emit audit-only.

    Gates only when a policy may be active (BLOCK / HITL, or pre-login where
    the mode is not yet known and the async gate would fail-closed) and this
    thread is not itself running an event loop -- blocking the event-loop
    thread would deadlock, so those callers are left to emit and pass through.
    """
    if not ws.policy_active() and ws._login_ack_received.is_set():  # pyright: ignore[reportPrivateUsage]
        return False

    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return True  # no loop on this thread: worker or pure-sync caller
    else:
        return False  # on an event-loop thread: must not block it


# ------------------------------------------------------------------
# Streaming (messages.stream)
# ------------------------------------------------------------------


def _safe_snapshot(stream: Any) -> Any:  # noqa: ANN401
    """Read a stream's accumulated message snapshot without raising.

    ``MessageStream.current_message_snapshot`` asserts the snapshot exists, so
    it raises for a stream that was never consumed.

    Args:
        stream: ``MessageStream`` or ``AsyncMessageStream``.

    Returns:
        The accumulated ``Message``, or ``None`` when unavailable.
    """
    try:
        return stream.current_message_snapshot
    except Exception:  # noqa: BLE001 - AssertionError when nothing accumulated
        return None


class _StreamGateState:
    """Runs emit + gate exactly once for one streamed message.

    ``MessageStream.get_final_message`` calls ``self.until_done()`` internally,
    and both are instrumented, so the run is once-guarded.  The guard is safe
    because :func:`_rewrite_blocked_response` mutates the message in place and
    both paths hold the same snapshot object: a second call returns a message
    that has already been gated and rewritten.
    """

    def __init__(self, kwargs: dict[str, Any], invocation_id: str | None) -> None:
        self._kwargs = kwargs
        self._invocation_id = invocation_id
        self._done = False

    @property
    def done(self) -> bool:
        """Whether emit + gate has already run for this stream."""
        return self._done

    def run_sync(self, message: Any) -> Any:  # noqa: ANN401
        """Emit and gate ``message`` from a synchronous consumer."""
        if self._done or message is None:
            return message

        self._done = True

        return _emit_and_gate_sync(
            message, self._kwargs, invocation_id=self._invocation_id
        )

    async def run_async(self, message: Any) -> Any:  # noqa: ANN401
        """Emit and gate ``message`` from an asynchronous consumer."""
        if self._done or message is None:
            return message

        self._done = True

        await _emit_pair(message, self._kwargs, invocation_id=self._invocation_id)

        return await _gate_response(message, self._kwargs)

    def emit_audit_only_sync(self, message: Any) -> None:  # noqa: ANN401
        """Emit without gating, for a stream consumed without a final message."""
        if self._done or message is None:
            return

        self._done = True
        _schedule_emit(message, self._kwargs, invocation_id=self._invocation_id)

    async def emit_audit_only_async(self, message: Any) -> None:  # noqa: ANN401
        """Async counterpart to :meth:`emit_audit_only_sync`."""
        if self._done or message is None:
            return

        self._done = True
        await _emit_pair(message, self._kwargs, invocation_id=self._invocation_id)


def _instrument_sync_stream(stream: Any, state: _StreamGateState) -> None:  # noqa: ANN401
    """Route a ``MessageStream``'s terminal methods through emit + gate.

    Rebinds ``get_final_message`` / ``until_done`` as instance attributes so the
    real SDK object is handed back to the caller untouched otherwise --
    ``text_stream``, ``response``, ``request_id`` and ``close`` keep working.

    Args:
        stream: The ``MessageStream`` returned by the manager's ``__enter__``.
        state: Once-guarded emit + gate runner for this stream.
    """
    original_final = stream.get_final_message
    original_until = stream.until_done

    def until_done() -> None:
        original_until()
        state.run_sync(_safe_snapshot(stream))

    def get_final_message() -> Any:  # noqa: ANN401
        # original_final() calls until_done() above, which already ran the
        # gate; the once-guard makes this a pass-through of the gated message.
        return state.run_sync(original_final())

    stream.until_done = until_done
    stream.get_final_message = get_final_message


def _instrument_async_stream(stream: Any, state: _StreamGateState) -> None:  # noqa: ANN401
    """Async counterpart to :func:`_instrument_sync_stream`."""
    original_final = stream.get_final_message
    original_until = stream.until_done

    async def until_done() -> None:
        await original_until()
        await state.run_async(_safe_snapshot(stream))

    async def get_final_message() -> Any:  # noqa: ANN401
        return await state.run_async(await original_final())

    stream.until_done = until_done
    stream.get_final_message = get_final_message


class _GatedMessageStreamManager:
    """Wraps ``MessageStreamManager`` so the streamed message is emitted + gated.

    ``client.messages.stream(...)`` returns a context manager rather than a
    response, so the instrumentation seam is ``__enter__``: the real
    ``MessageStream`` is handed back with its terminal methods rerouted (see
    :func:`_instrument_sync_stream`).

    ``__exit__`` is a safety net -- a consumer that only reads ``text_stream``
    never calls a terminal method, and would otherwise leave no audit trail.
    That emission is deliberately audit-only: by ``__exit__`` the caller has
    already seen every block, so gating there would change nothing.
    """

    def __init__(
        self,
        inner: Any,  # noqa: ANN401
        kwargs: dict[str, Any],
        invocation_id: str | None,
    ) -> None:
        self._inner = inner
        self._stream: Any = None
        self._state = _StreamGateState(kwargs, invocation_id)

    def __enter__(self) -> Any:  # noqa: ANN401
        stream = self._inner.__enter__()
        self._stream = stream

        try:
            _instrument_sync_stream(stream, self._state)
        except Exception:
            logger.exception("Failed to instrument Anthropic message stream")

        return stream

    def __exit__(self, *exc_info: Any) -> Any:  # noqa: ANN401
        try:
            if not self._state.done and self._stream is not None:
                self._state.emit_audit_only_sync(_safe_snapshot(self._stream))
        except Exception:
            logger.exception("Failed to emit Anthropic stream event on exit")

        return self._inner.__exit__(*exc_info)


class _GatedAsyncMessageStreamManager:
    """Async counterpart to :class:`_GatedMessageStreamManager`."""

    def __init__(
        self,
        inner: Any,  # noqa: ANN401
        kwargs: dict[str, Any],
        invocation_id: str | None,
    ) -> None:
        self._inner = inner
        self._stream: Any = None
        self._state = _StreamGateState(kwargs, invocation_id)

    async def __aenter__(self) -> Any:  # noqa: ANN401
        stream = await self._inner.__aenter__()
        self._stream = stream

        try:
            _instrument_async_stream(stream, self._state)
        except Exception:
            logger.exception("Failed to instrument Anthropic message stream")

        return stream

    async def __aexit__(self, *exc_info: Any) -> Any:  # noqa: ANN401
        try:
            if not self._state.done and self._stream is not None:
                await self._state.emit_audit_only_async(_safe_snapshot(self._stream))
        except Exception:
            logger.exception("Failed to emit Anthropic stream event on exit")

        return await self._inner.__aexit__(*exc_info)


# ------------------------------------------------------------------
# SDK patching
# ------------------------------------------------------------------


def patch_anthropic(
    hooks_getter: Callable[[], HookRegistry | None],
    config_getter: Callable[[], AdrianConfig | None],
    ws_getter: Callable[[], WebSocketClient | None] | None = None,
    handler_getter: Callable[[], AdrianCallbackHandler | None] | None = None,
) -> None:
    """Monkey-patch ``anthropic.Anthropic`` and ``anthropic.AsyncAnthropic``.

    Wraps ``messages.create`` and ``messages.stream`` on both the sync and async
    Anthropic resource classes so every API call is captured as an Adrian
    ``PairedEvent`` and, under ``MODE_BLOCK`` / ``MODE_HITL``, gated on the
    classifier verdict (see :func:`_gate_response`).  Both the sync and async
    paths gate; the sync path bridges onto the WebSocket loop (see
    :func:`_emit_and_gate_sync`).  For ``stream`` the gate runs when the caller
    asks for the final message (see :class:`_GatedMessageStreamManager`).

    The patch is idempotent: subsequent calls update the internal getters but
    do not re-wrap the already-patched methods.  If the ``anthropic`` package is
    not installed the call is a silent no-op.

    This function is called automatically by :func:`~adrian.init` when
    ``auto_instrument=True`` (the default).

    Args:
        hooks_getter: Zero-arg callable returning the current ``HookRegistry``,
            or ``None`` when the SDK is not initialised.
        config_getter: Zero-arg callable returning the current ``AdrianConfig``,
            or ``None`` when the SDK is not initialised.
        ws_getter: Zero-arg callable returning the current ``WebSocketClient``,
            or ``None``.  Required for verdict gating; when absent the handler
            stays audit-only (ALERT-mode behaviour).
        handler_getter: Zero-arg callable returning the current
            ``AdrianCallbackHandler``, or ``None``.  Enables the developer
            ``on_verdict`` / ``on_block`` / ``on_audit`` callbacks for Anthropic
            events by registering them in the handler's event map.
    """
    global _hooks_getter, _config_getter, _ws_getter, _handler_getter  # noqa: PLW0603

    _hooks_getter = hooks_getter
    _config_getter = config_getter
    _ws_getter = ws_getter
    _handler_getter = handler_getter

    try:
        from anthropic.resources.messages import AsyncMessages, Messages
    except ImportError:
        logger.debug("anthropic package not installed; skipping Anthropic patching")
        return

    # ---- sync Messages.create ----
    try:
        sync_cls = Messages

        if not getattr(sync_cls, "_adrian_patched", False):
            _original_sync = sync_cls.create

            def _patched_sync_create(
                self: Any,
                *args: Any,
                **kwargs: Any,  # noqa: ANN401
            ) -> Any:  # noqa: ANN401
                _request_summarised_thinking(kwargs)
                response = _original_sync(self, *args, **kwargs)

                # ChatAnthropic routes through here, but the LangChain
                # callbacks already emit and gate this call. Injecting
                # thinking above still applies, so the LangChain event
                # keeps its reasoning.
                if in_chat_model():
                    return response

                # Emit + gate together on the WS loop (BLOCK/HITL); degrades to
                # audit-only emission when gating isn't possible.
                return _emit_and_gate_sync(response, kwargs)

            _original_sync_stream = sync_cls.stream

            def _patched_sync_stream(
                self: Any,
                *args: Any,
                **kwargs: Any,  # noqa: ANN401
            ) -> Any:  # noqa: ANN401
                # Sampled now, not at consumption time: the caller may read the
                # final message after leaving the anthropic_invocation() block.
                captured = get_invocation_id()
                _request_summarised_thinking(kwargs)
                inner = _original_sync_stream(self, *args, **kwargs)

                # Owned by the LangChain callbacks: hand back the SDK's own
                # manager so nothing is emitted or gated twice.
                if in_chat_model():
                    return inner

                return _GatedMessageStreamManager(inner, kwargs, captured)

            sync_cls.create = _patched_sync_create  # type: ignore[method-assign]
            sync_cls.stream = _patched_sync_stream  # type: ignore[method-assign]
            sync_cls._adrian_patched = True  # type: ignore[attr-defined]
            logger.debug("Patched anthropic.resources.Messages.create / stream")
    except AttributeError:
        logger.warning(
            "Could not patch anthropic.resources.Messages; "
            "the SDK structure may have changed"
        )

    # ---- async AsyncMessages.create ----
    try:
        async_cls = AsyncMessages

        if not getattr(async_cls, "_adrian_patched", False):
            _original_async = async_cls.create

            async def _patched_async_create(
                self: Any,
                *args: Any,
                **kwargs: Any,  # noqa: ANN401
            ) -> Any:  # noqa: ANN401
                _request_summarised_thinking(kwargs)
                response = await _original_async(self, *args, **kwargs)

                # See the sync wrapper: the LangChain callbacks own this
                # call, so emitting here would double-count it.
                if in_chat_model():
                    return response

                # Emit first so the verdict future is registered, then gate:
                # under BLOCK/HITL this holds the response until the verdict
                # arrives and rewrites blocked tool calls to a [BLOCKED] block.
                await _emit_pair(response, kwargs)
                return await _gate_response(response, kwargs)

            _original_async_stream = async_cls.stream

            def _patched_async_stream(
                self: Any,
                *args: Any,
                **kwargs: Any,  # noqa: ANN401
            ) -> Any:  # noqa: ANN401
                # Not async: stream() returns an async context manager without
                # being awaited itself.
                captured = get_invocation_id()
                _request_summarised_thinking(kwargs)
                inner = _original_async_stream(self, *args, **kwargs)

                # See the sync stream wrapper.
                if in_chat_model():
                    return inner

                return _GatedAsyncMessageStreamManager(inner, kwargs, captured)

            async_cls.create = _patched_async_create  # type: ignore[method-assign]
            async_cls.stream = _patched_async_stream  # type: ignore[method-assign]
            async_cls._adrian_patched = True  # type: ignore[attr-defined]
            logger.debug("Patched anthropic.resources.AsyncMessages.create / stream")
    except AttributeError:
        logger.warning(
            "Could not patch anthropic.resources.AsyncMessages; "
            "the SDK structure may have changed"
        )


# ------------------------------------------------------------------
# Invocation context managers
# ------------------------------------------------------------------


@asynccontextmanager
async def anthropic_invocation():  # type: ignore[return]
    """Group async Anthropic API calls under a single invocation ID.

    Sets the ``invocation_id`` context variable so all ``messages.create``
    calls within the block share the same ID, enabling multi-turn agent
    conversations to be correlated in the Adrian dashboard.

    Usage::

        async with adrian.anthropic_invocation():
            r1 = await client.messages.create(...)
            r2 = await client.messages.create(...)  # same invocation_id as r1
    """
    token: Token[str | None] = set_invocation_id(str(uuid4()))

    try:
        yield
    finally:
        token.var.reset(token)


@contextmanager
def anthropic_invocation_sync():  # type: ignore[return]
    """Group synchronous Anthropic API calls under a single invocation ID.

    The sync counterpart to :func:`anthropic_invocation`.

    Usage::

        with adrian.anthropic_invocation_sync():
            r1 = client.messages.create(...)
            r2 = client.messages.create(...)  # same invocation_id as r1
    """
    token: Token[str | None] = set_invocation_id(str(uuid4()))

    try:
        yield
    finally:
        token.var.reset(token)
