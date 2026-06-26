# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
#
# Licensed under the Apache Licence, Version 2.0 (the "Licence").
# You may not use this file except in compliance with the Licence.
# A copy of the Licence is included at LICENSE in the repository root.
"""Anthropic SDK instrumentation for Adrian.

Patches ``anthropic.Anthropic`` and ``anthropic.AsyncAnthropic`` so that every
``messages.create`` call is captured as an Adrian ``PairedEvent`` and emitted
through the hook registry.  The patch is idempotent; calling
:func:`patch_anthropic` again after a shutdown / re-init only updates the
internal getters, it does not re-wrap the already-patched method.

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
from typing import TYPE_CHECKING, Any
from uuid import uuid4

from adrian.config import AdrianConfig
from adrian.context import get_invocation_id, set_invocation_id
from adrian.format.types import AgentContext, LlmPairData, PairedEvent
from adrian.hooks import HookRegistry
from adrian.types import ChatMessage, EventData, TokenUsage, ToolCallRecord

if TYPE_CHECKING:
    from contextvars import Token

logger = logging.getLogger("adrian.anthropic")

# Set once by patch_anthropic(); read at call time so shutdown + re-init works.
_hooks_getter: Callable[[], HookRegistry | None] | None = None
_config_getter: Callable[[], AdrianConfig | None] | None = None


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
        ),
    )


# ------------------------------------------------------------------
# Emission helpers
# ------------------------------------------------------------------


async def _emit_pair(response: Any, kwargs: dict[str, Any]) -> None:
    """Assemble and emit a ``PairedEvent`` for a completed ``messages.create`` call.

    Reads hooks and config at call time so the correct state is used even if
    :func:`~adrian.shutdown` and :func:`~adrian.init` have been called since
    the patch was applied.

    Args:
        response: Anthropic ``Message`` response object.
        kwargs: Original ``messages.create`` keyword arguments.
    """
    if _hooks_getter is None or _config_getter is None:
        return

    hooks = _hooks_getter()
    config = _config_getter()

    if hooks is None or config is None:
        return

    try:
        session_id = config.session_id
        messages_param: list[dict[str, Any]] = list(kwargs.get("messages") or [])
        system_param: str | list[Any] | None = kwargs.get("system")
        model_param: str = str(kwargs.get("model", "unknown"))

        flat_messages = _flatten_anthropic_messages(messages_param, system_param)
        invocation_id = get_invocation_id() or "no_invocation"
        run_id = str(uuid4())

        pair = build_anthropic_llm_pair(
            flat_messages=flat_messages,
            response=response,
            model=model_param,
            session_id=session_id,
            invocation_id=invocation_id,
            run_id=run_id,
        )

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


def _schedule_emit(response: Any, kwargs: dict[str, Any]) -> None:
    """Schedule event emission from a synchronous call site.

    When inside a running event loop, schedules a fire-and-forget task so
    the sync caller is not blocked.  When no loop is running, blocks until
    emission completes so the event is not silently dropped.

    Args:
        response: Anthropic ``Message`` response object.
        kwargs: Original ``messages.create`` keyword arguments.
    """
    coro = _emit_pair(response, kwargs)

    try:
        loop = asyncio.get_running_loop()
        loop.create_task(coro)
    except RuntimeError:
        try:
            asyncio.run(coro)
        except Exception:
            logger.exception("Failed to emit Anthropic event (sync path)")


# ------------------------------------------------------------------
# SDK patching
# ------------------------------------------------------------------


def patch_anthropic(
    hooks_getter: Callable[[], HookRegistry | None],
    config_getter: Callable[[], AdrianConfig | None],
) -> None:
    """Monkey-patch ``anthropic.Anthropic`` and ``anthropic.AsyncAnthropic``.

    Wraps ``messages.create`` on both the sync and async Anthropic resource
    classes so every API call is captured as an Adrian ``PairedEvent``.

    The patch is idempotent: subsequent calls update the internal getters but
    do not re-wrap the already-patched method.  If the ``anthropic`` package is
    not installed the call is a silent no-op.

    This function is called automatically by :func:`~adrian.init` when
    ``auto_instrument=True`` (the default).

    Args:
        hooks_getter: Zero-arg callable returning the current ``HookRegistry``,
            or ``None`` when the SDK is not initialised.
        config_getter: Zero-arg callable returning the current ``AdrianConfig``,
            or ``None`` when the SDK is not initialised.
    """
    global _hooks_getter, _config_getter  # noqa: PLW0603

    _hooks_getter = hooks_getter
    _config_getter = config_getter

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
                response = _original_sync(self, *args, **kwargs)
                _schedule_emit(response, kwargs)
                return response

            sync_cls.create = _patched_sync_create  # type: ignore[method-assign]
            sync_cls._adrian_patched = True  # type: ignore[attr-defined]
            logger.debug("Patched anthropic.resources.Messages.create")
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
                response = await _original_async(self, *args, **kwargs)
                await _emit_pair(response, kwargs)
                return response

            async_cls.create = _patched_async_create  # type: ignore[method-assign]
            async_cls._adrian_patched = True  # type: ignore[attr-defined]
            logger.debug("Patched anthropic.resources.AsyncMessages.create")
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
