# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""PairedEvent PII redactor for protobuf messages.

The SDK's redactor operates on ``adrian.format.types`` dataclasses and
wraps event handlers.  The plugin builds protobuf ``PairedEvent``
messages and writes them straight to the socket, so this module
redacts the protobuf fields in place instead.

Field coverage matches the SDK's ``PiiRedactor``: agent and parent
context prompts, tool input/output, and LLM messages, output and
tool-call args.  It additionally covers the free-text entries of
``metadata_json``, which the SDK's redactor does not walk and which
the plugin uses to carry model reasoning.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

from adrian_cc.pii._engine import PiiConfig, redact_text

if TYPE_CHECKING:
    from adrian_cc.proto import event_pb2 as pb

# Metadata keys holding free model or user text.  Everything else in
# metadata_json is structural (ids, paths, counts, modes) and is left
# alone so the backend and dashboard can still parse it.
_FREE_TEXT_METADATA_KEYS = ("reasoning_latest",)


class PiiRedactor:
    """Redacts PII from protobuf ``PairedEvent`` messages.

    Args:
        config: PII redaction configuration.  Uses defaults if ``None``
            (REPLACE strategy, all types enabled).
    """

    def __init__(self, config: PiiConfig | None = None) -> None:
        """Initialise with redaction config.

        Args:
            config: PII redaction settings.  Uses defaults if ``None``.
        """
        self._config = config or PiiConfig()

    def redact_event(self, event: pb.PairedEvent) -> None:
        """Redact every free-text field on ``event``, in place.

        Args:
            event: The protobuf event to redact.  Mutated directly.
        """
        self._redact_context(event.agent)

        # HasField, not truthiness: reading event.parent would hand back
        # a default instance and assigning into it would materialise an
        # empty submessage on the wire.
        if event.HasField("parent"):
            self._redact_context(event.parent)

        match event.WhichOneof("data"):
            case "tool":
                self._redact_tool(event.tool)
            case "llm":
                self._redact_llm(event.llm)
            case _:
                # No payload set yet, or a oneof arm added to the proto
                # after this code was written.
                pass

        self._redact_metadata(event)

    def _redact_str(self, text: str) -> str:
        """Redact PII in a single string."""
        return redact_text(text, self._config).text

    def _apply(self, message: object, field: str) -> None:
        """Redact ``message.field`` only when the value actually changes.

        Skipping no-op writes keeps clean events byte-identical to what
        they would have been without redaction.

        Args:
            message: Protobuf message holding the field.
            field: Name of the string field to redact.
        """
        current: str = getattr(message, field)

        if not current:
            return

        redacted = self._redact_str(current)

        if redacted != current:
            setattr(message, field, redacted)

    def _redact_context(self, ctx: pb.AgentContext) -> None:
        """Redact system prompt and user instruction in-place."""
        self._apply(ctx, "system_prompt")
        self._apply(ctx, "user_instruction")

    def _redact_tool(self, tool: pb.ToolPairData) -> None:
        """Redact tool input and output in-place."""
        self._apply(tool, "input")
        self._apply(tool, "output")

    def _redact_llm(self, llm: pb.LlmPairData) -> None:
        """Redact LLM messages, output and tool-call args in-place."""
        for msg in llm.messages:
            self._apply(msg, "content")

        self._apply(llm, "output")

        for call in llm.tool_calls:
            self._apply(call, "args")

    def _redact_metadata(self, event: pb.PairedEvent) -> None:
        """Redact the free-text entries of ``metadata_json`` in-place."""
        raw = event.metadata_json

        if not raw:
            return

        try:
            parsed: Any = json.loads(raw)
        except (ValueError, UnicodeDecodeError):
            # Always this plugin's own json.dumps output, so a parse
            # failure means the blob is corrupt. Leave it rather than
            # discard the event's whole metadata.
            return

        if not isinstance(parsed, dict):
            return

        meta = cast("dict[str, Any]", parsed)
        changed = False

        for key in _FREE_TEXT_METADATA_KEYS:
            value = meta.get(key)

            if not isinstance(value, str) or not value:
                continue

            redacted = self._redact_str(value)

            if redacted != value:
                meta[key] = redacted
                changed = True

        if changed:
            event.metadata_json = json.dumps(meta, default=str).encode()


def redact_event(
    event: pb.PairedEvent,
    config: PiiConfig | None = None,
) -> None:
    """Redact PII from a ``PairedEvent`` in place.

    Convenience wrapper over ``PiiRedactor.redact_event`` for callers
    that do not need to hold on to a redactor.

    Args:
        event: The protobuf event to redact.  Mutated directly.
        config: PII redaction settings.  Uses defaults if ``None``.
    """
    PiiRedactor(config).redact_event(event)
