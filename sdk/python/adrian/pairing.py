# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Event pair buffer.

Buffers start events (chat_model_start, tool_start) and assembles
complete ``PairedEvent`` instances when the corresponding end event
(llm_end, tool_end) arrives. Multiple start events can be buffered
simultaneously with different ``run_id`` values, this is required
for parallel agent execution (e.g. router fan-out via Send).

The buffer is the core state machine that transforms raw LangChain
callbacks into the Adrian unified format.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import uuid4

from adrian.format.types import (
    AgentContext,
    LlmPairData,
    PairedEvent,
    ParentContext,
    ToolPairData,
)
from adrian.types import (
    ChatMessage,
    ChatModelStartData,
    LlmEndData,
    ToolCallRecord,
    ToolEndData,
    ToolStartData,
)

logger = logging.getLogger("adrian.pairing")


@dataclass(slots=True)
class StartEventRecord:
    """Buffered start event waiting for its matching end.

    Attributes:
        event_type: ``"chat_model_start"`` or ``"tool_start"``.
        data: Raw event data TypedDict from the callback.
        agent_id: Derived agent identity.
        parent_run_id: LangChain parent_run_id from the callback.
        parent: Parent context if this is a sub-agent, or ``None``.
        metadata: Raw framework callback metadata.
    """

    event_type: str
    data: ChatModelStartData | ToolStartData
    agent_id: str
    parent_run_id: str
    parent: ParentContext | None
    metadata: dict[str, Any] | None
    agent_context: AgentContext | None = None


class EventPairBuffer:
    """Buffers start events and assembles pairs on end events.

    Thread-safe for the async case (single event loop) but not for
    multi-threaded sync callbacks. If sync support is needed later,
    add a lock around ``_pending``.
    """

    def __init__(self) -> None:
        """Initialise with an empty pending buffer."""
        self._pending: dict[str, StartEventRecord] = {}

    def on_start(
        self,
        event_type: str,
        data: ChatModelStartData | ToolStartData,
        run_id: str,
        agent_id: str,
        parent: ParentContext | None,
        metadata: dict[str, Any] | None,
        agent_context: AgentContext | None = None,
        parent_run_id: str = "",
    ) -> None:
        """Buffer a start event for later pairing.

        Args:
            event_type: ``"chat_model_start"`` or ``"tool_start"``.
            data: Raw event data from the callback.
            run_id: LangChain run_id that links start+end.
            agent_id: Derived agent identity.
            parent: Parent agent context, or ``None``.
            metadata: Raw framework callback metadata.
            agent_context: Full agent context for tool events to
                inherit the system prompt and user instruction from
                the LLM that requested them.
            parent_run_id: LangChain parent_run_id from the callback.
                For tool starts this is the run_id of the LLM that
                requested the tool, the block-mode correlation key.
        """
        self._pending[run_id] = StartEventRecord(
            event_type=event_type,
            data=data,
            agent_id=agent_id,
            parent_run_id=parent_run_id,
            parent=parent,
            metadata=metadata,
            agent_context=agent_context,
        )

    def on_end(
        self,
        event_type: str,
        data: LlmEndData | ToolEndData,
        run_id: str,
        invocation_id: str,
        session_id: str,
    ) -> PairedEvent | None:
        """Match an end event with its buffered start and assemble a pair.

        Pops the matching start event from the buffer. If no matching
        start exists (orphan end event), logs a warning and returns
        ``None``.

        Args:
            event_type: ``"llm_end"`` or ``"tool_end"``.
            data: Raw end event data from the callback.
            run_id: LangChain run_id to match with a buffered start.
            invocation_id: Invocation correlation ID.
            session_id: Session identifier.

        Returns:
            Assembled ``PairedEvent``, or ``None`` if no matching start.
        """
        start = self._pending.pop(run_id, None)

        if start is None:
            logger.warning("orphan end event: %s run_id=%s", event_type, run_id[:12])
            return None

        if event_type == "llm_end" and start.event_type == "chat_model_start":
            return self._assemble_llm_pair(
                start, data, run_id, invocation_id, session_id
            )

        if event_type == "tool_end" and start.event_type == "tool_start":
            return self._assemble_tool_pair(
                start, data, run_id, invocation_id, session_id
            )

        logger.warning(
            "mismatched pair: start=%s end=%s run_id=%s",
            start.event_type,
            event_type,
            run_id[:12],
        )

        return None

    def _assemble_llm_pair(
        self,
        start: StartEventRecord,
        end_data: LlmEndData | ToolEndData,
        run_id: str,
        invocation_id: str,
        session_id: str,
    ) -> PairedEvent:
        """Combine chat_model_start + llm_end into an LLM PairedEvent.

        Extracts the system prompt and user instruction from the start
        event's messages to populate the ``AgentContext``.

        Args:
            start: Buffered start event record.
            end_data: llm_end event data.
            run_id: Shared run_id.
            invocation_id: Invocation correlation ID.
            session_id: Session identifier.

        Returns:
            Assembled LLM ``PairedEvent``.
        """
        start_data: ChatModelStartData = start.data  # type: ignore[assignment]
        llm_end: LlmEndData = end_data  # type: ignore[assignment]

        messages: list[ChatMessage] = start_data.get("messages", [])
        system_prompt = ""
        user_instruction = ""

        for msg in messages:
            if msg.get("role") == "system" and not system_prompt:
                system_prompt = msg["content"]

        for msg in reversed(messages):
            if msg.get("role") in ("human", "user"):
                user_instruction = msg["content"]

                break

        tool_calls: list[ToolCallRecord] = llm_end.get("tool_calls", [])

        return PairedEvent(
            event_id=str(uuid4()),
            invocation_id=invocation_id,
            session_id=session_id,
            run_id=run_id,
            timestamp=datetime.now(UTC).isoformat(),
            pair_type="llm",
            agent=AgentContext(
                agent_id=start.agent_id,
                system_prompt=system_prompt,
                user_instruction=user_instruction,
            ),
            parent=start.parent,
            data=LlmPairData(
                model=start_data.get("model", "unknown"),
                messages=messages,
                output=llm_end.get("output", ""),
                tool_calls=tool_calls,
                usage=llm_end.get("usage"),
            ),
            parent_run_id=start.parent_run_id,
            metadata=start.metadata,
        )

    def _assemble_tool_pair(
        self,
        start: StartEventRecord,
        end_data: LlmEndData | ToolEndData,
        run_id: str,
        invocation_id: str,
        session_id: str,
    ) -> PairedEvent:
        """Combine tool_start + tool_end into a Tool PairedEvent.

        The agent context is inherited from the start event, which
        received it from the preceding LLM event's agent_id.

        Args:
            start: Buffered start event record.
            end_data: tool_end event data.
            run_id: Shared run_id.
            invocation_id: Invocation correlation ID.
            session_id: Session identifier.

        Returns:
            Assembled Tool ``PairedEvent``.
        """
        tool_start: ToolStartData = start.data  # type: ignore[assignment]
        tool_end: ToolEndData = end_data  # type: ignore[assignment]

        return PairedEvent(
            event_id=str(uuid4()),
            invocation_id=invocation_id,
            session_id=session_id,
            run_id=run_id,
            timestamp=datetime.now(UTC).isoformat(),
            pair_type="tool",
            agent=start.agent_context or AgentContext(agent_id=start.agent_id),
            parent=start.parent,
            data=ToolPairData(
                tool_name=tool_start.get("tool_name", "unknown"),
                tool_call_id=tool_start.get("tool_call_id"),
                input=tool_start.get("input", ""),
                output=tool_end.get("output", ""),
            ),
            parent_run_id=start.parent_run_id,
            metadata=start.metadata,
        )
