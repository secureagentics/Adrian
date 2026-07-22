# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""PairedEvent PII redactor.

Applies PII redaction to text fields within a ``PairedEvent``'s
data payload, agent context, and parent context.  Also provides
``RedactingHandler``, an ``EventHandler`` wrapper that redacts
events before forwarding them to a downstream handler.
"""

from __future__ import annotations

from copy import deepcopy
from typing import TYPE_CHECKING, Any

from adrian.format.types import (
    AgentContext,
    LlmPairData,
    PairedEvent,
    ParentContext,
    ToolPairData,
)
from adrian.pii._engine import PiiConfig, redact_text
from adrian.pii._patterns import detect

if TYPE_CHECKING:
    from adrian.hooks import EventHandler


class PiiRedactor:
    """Redacts PII from ``PairedEvent`` objects.

    Args:
        config: PII redaction configuration.
    """

    def __init__(self, config: PiiConfig | None = None) -> None:
        """Initialise with redaction config.

        Args:
            config: PII redaction settings.  Uses defaults if ``None``.
        """
        self._config = config or PiiConfig()

    def redact_event(
        self,
        event: PairedEvent,
        *,
        in_place: bool = False,
    ) -> PairedEvent:
        """Redact PII from all text fields in a PairedEvent.

        Args:
            event: The paired event to redact.
            in_place: If ``True``, mutate the event directly.  If
                ``False``, return a deep copy with redactions applied.

        Returns:
            The redacted ``PairedEvent`` (same object if in_place).
        """
        target = event if in_place else deepcopy(event)

        self._redact_agent_context(target.agent)

        if target.parent is not None:
            self._redact_parent_context(target.parent)

        if isinstance(target.data, LlmPairData):
            self._redact_llm_data(target.data)
        else:
            # Union is LlmPairData | ToolPairData; this branch is the
            # ToolPairData case.
            self._redact_tool_data(target.data)

        return target

    def event_has_pii(self, event: PairedEvent) -> bool:
        """Return True if any text field in the event contains PII.

        Used as a cheap pre-check so RedactingHandler can skip the
        deepcopy when the event is clean.
        """
        for text in self._iter_event_text(event):
            if detect(text, types=self._config.enabled_types):
                return True
        return False

    def _iter_event_text(self, event: PairedEvent) -> list[str]:
        """Collect every string field that would be scanned for PII."""
        texts: list[str] = [
            event.agent.system_prompt,
            event.agent.user_instruction,
        ]

        if event.parent is not None:
            texts.append(event.parent.system_prompt)
            texts.append(event.parent.user_instruction)

        if isinstance(event.data, LlmPairData):
            for msg in event.data.messages:
                texts.append(msg["content"])
            texts.append(event.data.output)
            for tc in event.data.tool_calls:
                _collect_strings(tc.get("args", {}), texts)
        else:
            texts.append(event.data.input)
            texts.append(event.data.output)

        return texts

    def _redact_str(self, text: str) -> str:
        """Redact PII in a single string."""
        return redact_text(text, self._config).text

    def _redact_value(self, value: Any) -> Any:  # noqa: ANN401
        """Return a new value with strings redacted recursively.

        Containers (dict, list, tuple, set, frozenset) are rebuilt;
        the input value is never mutated.  Non-string, non-container
        scalars (int, float, bool, None, ...) pass through unchanged.
        """
        match value:
            case str():
                return self._redact_str(value)
            case dict():
                return {k: self._redact_value(v) for k, v in value.items()}  # pyright: ignore[reportUnknownVariableType]
            case list():
                return [self._redact_value(v) for v in value]  # pyright: ignore[reportUnknownVariableType]
            case tuple():
                return tuple(self._redact_value(v) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            case frozenset():
                return frozenset(self._redact_value(v) for v in value)  # pyright: ignore[reportUnknownVariableType, reportUnknownArgumentType]
            case set():
                return {self._redact_value(v) for v in value}  # pyright: ignore[reportUnknownVariableType]
            case _:
                return value

    def _redact_llm_data(self, data: LlmPairData) -> None:
        """Redact PII fields within ``LlmPairData`` in-place."""
        for msg in data.messages:
            msg["content"] = self._redact_str(msg["content"])

        data.output = self._redact_str(data.output)

        for tc in data.tool_calls:
            args = tc["args"]

            for key, val in args.items():
                args[key] = self._redact_value(val)  # type: ignore[assignment]

    def _redact_tool_data(self, data: ToolPairData) -> None:
        """Redact PII fields within ``ToolPairData`` in-place."""
        data.input = self._redact_str(data.input)
        data.output = self._redact_str(data.output)

    def _redact_agent_context(self, ctx: AgentContext) -> None:
        """Redact system prompt and user instruction in-place."""
        ctx.system_prompt = self._redact_str(ctx.system_prompt)
        ctx.user_instruction = self._redact_str(ctx.user_instruction)

    def _redact_parent_context(self, ctx: ParentContext) -> None:
        """Redact parent context prompts in-place."""
        ctx.system_prompt = self._redact_str(ctx.system_prompt)
        ctx.user_instruction = self._redact_str(ctx.user_instruction)


def _collect_strings(value: Any, sink: list[str]) -> None:  # noqa: ANN401
    """Append every string contained in ``value`` (recursing) to ``sink``."""
    match value:
        case str():
            sink.append(value)
        case dict():
            for v in value.values():  # pyright: ignore[reportUnknownVariableType]
                _collect_strings(v, sink)
        case list() | tuple() | set() | frozenset():
            for v in value:  # pyright: ignore[reportUnknownVariableType]
                _collect_strings(v, sink)
        case _:
            pass


class RedactingHandler:
    """``EventHandler`` wrapper that redacts PII before delegation.

    Wraps an existing handler and applies PII redaction to each
    ``PairedEvent`` before passing it downstream.  Implements the
    ``EventHandler`` protocol.

    Args:
        inner: Downstream handler to forward redacted events to.
        config: PII redaction configuration.
    """

    def __init__(
        self,
        inner: EventHandler,
        config: PiiConfig | None = None,
    ) -> None:
        """Initialise with a downstream handler and config.

        Args:
            inner: The handler to forward redacted events to.
            config: PII redaction settings.
        """
        self._inner = inner
        self._redactor = PiiRedactor(config)

    async def on_paired_event(self, event: PairedEvent) -> None:
        """Redact PII (if any) and forward to the downstream handler.

        Skips the deepcopy when no PII is detected, so clean events
        cost only the scan.
        """
        if not self._redactor.event_has_pii(event):
            await self._inner.on_paired_event(event)
            return

        redacted = self._redactor.redact_event(event, in_place=False)
        await self._inner.on_paired_event(redacted)

    async def close(self) -> None:
        """Close the downstream handler."""
        await self._inner.close()
