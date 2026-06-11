"""LangChain async callback handler that produces Adrian PairedEvents.

Captures five LangChain callback events, pairs them (chat_model_start +
llm_end, tool_start + tool_end), enriches with agent identity and parent
context, and emits ``PairedEvent`` objects through the hook registry.
"""

import asyncio
import logging
from typing import Any, cast
from uuid import UUID

from langchain_core.callbacks import AsyncCallbackHandler
from langchain_core.messages import BaseMessage
from langchain_core.outputs import ChatGeneration, LLMResult

from adrian.config import AdrianConfig
from adrian.context import AgentContextTracker, get_invocation_id
from adrian.format.types import PairedEvent
from adrian.hooks import HookRegistry
from adrian.identity import derive_agent_id
from adrian.pairing import EventPairBuffer
from adrian.proto import event_pb2 as pb
from adrian.types import (
    CallbackMetadata,
    ChatMessage,
    ChatModelStartData,
    EventData,
    EventRecord,
    LlmEndData,
    TokenUsage,
    ToolArgs,
    ToolCallRecord,
    ToolEndData,
    ToolStartData,
    VerdictContext,
)

logger = logging.getLogger("adrian.handler")


class AdrianCallbackHandler(AsyncCallbackHandler):
    """Async callback handler that produces Adrian PairedEvents.

    Captures LangChain callbacks, buffers start events, and assembles
    complete PairedEvents when end events arrive. Events are emitted
    through the hook registry to all registered handlers.

    Args:
        pair_buffer: Buffer for pairing start+end events by run_id.
        context_tracker: Tracks agent identity and parent relationships.
        hooks: Registry of event handlers to emit through.
        config: Adrian SDK configuration with callback references.
    """

    def __init__(
        self,
        pair_buffer: EventPairBuffer,
        context_tracker: AgentContextTracker,
        hooks: HookRegistry,
        config: AdrianConfig,
    ) -> None:
        """Initialise the handler with pairing and context components.

        Args:
            pair_buffer: Buffer for pairing start+end events.
            context_tracker: Tracks agent context for parent enrichment.
            hooks: Registry of handlers to emit paired events through.
            config: Adrian SDK configuration.
        """
        super().__init__()
        self._pair_buffer = pair_buffer
        self._context_tracker = context_tracker
        self._hooks = hooks
        self._config = config

        # Verdict enrichment map (dormant until WebSocketHandler is added)
        self._event_map: dict[str, EventRecord] = {}

        # Current agent_id for tool events to inherit
        self._current_agent_id: str = "default"

    async def _emit_pair(self, pair: PairedEvent) -> None:
        """Emit an assembled PairedEvent through all registered handlers.

        Also fires the ``on_event`` callback if configured, and stores
        the event in the event map for future verdict enrichment.

        Args:
            pair: The assembled paired event.
        """
        await self._hooks.emit(pair)

        self._event_map[pair.event_id] = EventRecord(
            event_type=pair.pair_type,
            data=cast(EventData, pair.data),
            run_id=pair.run_id,
            parent_run_id=None,
        )

        if self._config.on_event is not None:
            result = self._config.on_event(
                pair.pair_type,
                cast(EventData, pair.data),
                pair.run_id,
                None,
                pair.event_id,
            )

            if asyncio.iscoroutine(result):
                await result

    async def handle_verdict(self, verdict: pb.Verdict) -> None:
        """Fire callbacks for a verdict.

        Called from the WebSocket recv loop for every verdict frame.
        Builds a :class:`VerdictContext` from the proto + the original
        event-data record, fires ``on_verdict`` always, then fires
        ``on_audit`` for M2-tier verdicts and ``on_block`` for M3/M4
        tier verdicts.  Both per-tier callbacks are notification-only;
        return values are ignored.
        """
        record = self._event_map.pop(verdict.event_id, None)

        if record is None:
            logger.warning(
                "Verdict for unknown event_id=%s, skipping callbacks",
                verdict.event_id,
            )

            return

        hitl: pb.HitlResponse | None = (
            verdict.hitl if verdict.HasField("hitl") else None
        )
        ctx = VerdictContext(
            event_id=verdict.event_id,
            session_id=verdict.session_id,
            event_type=record.event_type,
            event_data=record.data,
            run_id=record.run_id,
            parent_run_id=record.parent_run_id,
            policy=verdict.policy,
            mad_code=verdict.mad_code,
            hitl=hitl,
        )

        cfg = self._config
        mad_prefix = verdict.mad_code[:2]

        if cfg.on_verdict is not None:
            result = cfg.on_verdict(ctx)

            if asyncio.iscoroutine(result):
                await result

        # M3 and M4 being "suspicious" and "malicious" respectively fire on_block
        if mad_prefix in ("M3", "M4") and cfg.on_block is not None:
            result = cfg.on_block(ctx)

            if asyncio.iscoroutine(result):
                await result

        # M2 warrants a warning/audit so it fires on_audit
        if mad_prefix == "M2" and cfg.on_audit is not None:
            result = cfg.on_audit(ctx)

            if asyncio.iscoroutine(result):
                await result

    def _resolve_session_id(self) -> str:
        """Get the session_id from config.

        Returns:
            Session ID string.

        Raises:
            RuntimeError: If config has no session_id set.
        """
        session_id = self._config.session_id

        if not session_id:
            msg = "session_id is not set, adrian.init() must be called before capturing events"
            raise RuntimeError(msg)

        return session_id

    def _resolve_invocation_id(self) -> str:
        """Get the invocation_id from async context.

        Returns:
            Invocation ID string, or a generated warning placeholder.
        """
        invocation_id = get_invocation_id()

        if invocation_id is None:
            # Direct model calls (outside a LangGraph Pregel context)
            # still emit; we just can't link the event to an invocation.
            # INFO not WARN: nothing is dropped.
            logger.info(
                "LLM called outside of Pregel context; event will be "
                "emitted with invocation_id=no_invocation."
            )

            return "no_invocation"

        return invocation_id

    # ------------------------------------------------------------------
    # 1. chat_model_start
    # ------------------------------------------------------------------

    async def on_chat_model_start(
        self,
        serialized: dict[str, Any],
        messages: list[list[BaseMessage]],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture chat model invocation, extract agent identity, buffer start.

        Flattens the batched message list, derives agent_id from callback
        metadata, updates the context tracker to detect parent relationships,
        and buffers the start event for pairing with the upcoming llm_end.

        Args:
            serialized: Serialized model metadata.
            messages: Batched message lists from LangChain.
            run_id: LangChain run ID.
            parent_run_id: Parent run ID if nested.
            **kwargs: Additional LangChain callback kwargs.
        """
        flat_messages: list[ChatMessage] = [
            ChatMessage(
                role=str(getattr(m, "type", "unknown")),
                content=str(cast(object, m.content)),
            )
            for batch in messages
            for m in batch
        ]

        metadata = _extract_metadata(kwargs)
        agent_id = derive_agent_id(metadata, flat_messages)
        self._current_agent_id = agent_id

        system_prompt = ""
        user_instruction = ""

        for msg in flat_messages:
            if msg.get("role") == "system" and not system_prompt:
                system_prompt = msg["content"]

        for msg in reversed(flat_messages):
            if msg.get("role") in ("human", "user"):
                user_instruction = msg["content"]

                break

        parent = self._context_tracker.update(agent_id, system_prompt, user_instruction)

        data = _build_llm_start_data(
            model=extract_model_name(serialized),
            messages=flat_messages,
            metadata=metadata,
        )

        raw_metadata: dict[str, Any] | None = None

        if metadata:
            raw_metadata = dict(metadata)

        self._pair_buffer.on_start(
            event_type="chat_model_start",
            data=data,
            run_id=str(run_id),
            agent_id=agent_id,
            parent=parent or self._context_tracker.get_parent(agent_id),
            metadata=raw_metadata,
            parent_run_id=str(parent_run_id) if parent_run_id else "",
        )

    # ------------------------------------------------------------------
    # 2. llm_start (legacy text completions)
    # ------------------------------------------------------------------

    async def on_llm_start(
        self,
        serialized: dict[str, Any],
        prompts: list[str],
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture legacy text LLM invocation, buffer as chat_model_start.

        Converts raw text prompts to ChatMessage format and buffers
        as if it were a chat_model_start event.

        Args:
            serialized: Serialized model metadata.
            prompts: Raw text prompts.
            run_id: LangChain run ID.
            parent_run_id: Parent run ID if nested.
            **kwargs: Additional LangChain callback kwargs.
        """
        flat_messages: list[ChatMessage] = [
            ChatMessage(role="human", content=text) for text in prompts
        ]

        metadata = _extract_metadata(kwargs)
        agent_id = derive_agent_id(metadata, flat_messages)
        self._current_agent_id = agent_id

        parent = self._context_tracker.update(
            agent_id, "", prompts[0] if prompts else ""
        )

        data = _build_llm_start_data(
            model=extract_model_name(serialized),
            messages=flat_messages,
            metadata=metadata,
        )

        raw_metadata: dict[str, Any] | None = None

        if metadata:
            raw_metadata = dict(metadata)

        self._pair_buffer.on_start(
            event_type="chat_model_start",
            data=data,
            run_id=str(run_id),
            agent_id=agent_id,
            parent=parent or self._context_tracker.get_parent(agent_id),
            metadata=raw_metadata,
            parent_run_id=str(parent_run_id) if parent_run_id else "",
        )

    # ------------------------------------------------------------------
    # 3. llm_end
    # ------------------------------------------------------------------

    async def on_llm_end(
        self,
        response: LLMResult,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture LLM response and complete the LLM pair.

        Extracts output text, tool calls, and token usage from the
        LLM result, then passes to the pair buffer. If a matching
        chat_model_start was buffered, a PairedEvent is assembled
        and emitted through the hook registry.

        Args:
            response: LLM result containing generations and metadata.
            run_id: LangChain run ID.
            parent_run_id: Parent run ID if nested.
            **kwargs: Additional LangChain callback kwargs.
        """
        output = ""
        typed_tool_calls: list[ToolCallRecord] = []

        if response.generations:
            gen = response.generations[0][0]
            output = gen.text

            if isinstance(gen, ChatGeneration):
                msg = gen.message
                raw_calls: list[dict[str, str | dict[str, object]]] = (
                    getattr(msg, "tool_calls", None) or []
                )
                typed_tool_calls = [
                    ToolCallRecord(
                        id=str(tc.get("id", "")),
                        name=str(tc.get("name", "")),
                        args=cast(ToolArgs, tc.get("args", {})),
                    )
                    for tc in raw_calls
                ]

        usage: TokenUsage | None = None
        llm_output = cast(dict[str, object], response.llm_output or {})
        usage_raw = llm_output.get("token_usage")

        if isinstance(usage_raw, dict):
            usage_dict = cast(dict[str, int], usage_raw)
            usage = TokenUsage(
                prompt_tokens=usage_dict.get("prompt_tokens", 0),
                completion_tokens=usage_dict.get("completion_tokens", 0),
                total_tokens=usage_dict.get("total_tokens", 0),
            )

        data: LlmEndData = {
            "output": output,
            "tool_calls": typed_tool_calls,
            "usage": usage,
        }

        session_id = self._resolve_session_id()
        invocation_id = self._resolve_invocation_id()

        pair = self._pair_buffer.on_end(
            event_type="llm_end",
            data=data,
            run_id=str(run_id),
            invocation_id=invocation_id,
            session_id=session_id,
        )

        if pair:
            if typed_tool_calls:
                self._context_tracker.mark_delegated(pair.agent.agent_id)

            await self._emit_pair(pair)

    # ------------------------------------------------------------------
    # 4. tool_start
    # ------------------------------------------------------------------

    async def on_tool_start(
        self,
        serialized: dict[str, Any],
        input_str: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture tool invocation and buffer for pairing.

        Derives agent_id from the tool's own callback metadata first
        (which LangGraph provides), falling back to the last seen
        agent_id for non-LangGraph frameworks. This avoids the race
        condition where parallel agents overwrite ``_current_agent_id``.

        Args:
            serialized: Serialized tool metadata.
            input_str: Serialized input string.
            run_id: LangChain run ID.
            parent_run_id: Parent run ID if nested.
            **kwargs: Additional LangChain callback kwargs.
        """
        metadata = _extract_metadata(kwargs)

        # Try metadata first, but only use the result if it matches a known
        # agent (one that produced a chat_model_start). Graph-level ToolNodes
        # have their own checkpoint_ns ("tools", "specialist_tools") which
        # differs from the agent that requested the tool. Tools invoked inside
        # agent node functions (e.g. S3 router) inherit the parent node's
        # context and correctly resolve to the agent's id.
        agent_id = self._current_agent_id

        if metadata:
            candidate = derive_agent_id(metadata)

            if self._context_tracker.has_context(candidate):
                agent_id = candidate

        data: ToolStartData = {
            "tool_name": serialized.get("name", "unknown"),
            "tool_call_id": kwargs.get("tool_call_id"),
            "input": input_str,
            "metadata": metadata,
        }

        raw_metadata: dict[str, Any] | None = None

        if metadata:
            raw_metadata = dict(metadata)

        # Look up the full agent context so the tool pair inherits
        # the system prompt and user instruction from the LLM that
        # requested this tool call.
        full_context = self._context_tracker.get_context(agent_id)

        self._pair_buffer.on_start(
            event_type="tool_start",
            data=data,
            run_id=str(run_id),
            agent_id=agent_id,
            parent=self._context_tracker.get_parent(agent_id),
            metadata=raw_metadata,
            agent_context=full_context,
            parent_run_id=str(parent_run_id) if parent_run_id else "",
        )

    # ------------------------------------------------------------------
    # 5. tool_end
    # ------------------------------------------------------------------

    async def on_tool_end(
        self,
        output: str,
        *,
        run_id: UUID,
        parent_run_id: UUID | None = None,
        **kwargs: Any,
    ) -> None:
        """Capture tool result and complete the tool pair.

        Passes the output to the pair buffer. If a matching tool_start
        was buffered, a PairedEvent is assembled and emitted.

        Args:
            output: Tool output string.
            run_id: LangChain run ID.
            parent_run_id: Parent run ID if nested.
            **kwargs: Additional LangChain callback kwargs.
        """
        data: ToolEndData = {"output": str(output)}

        session_id = self._resolve_session_id()
        invocation_id = self._resolve_invocation_id()

        pair = self._pair_buffer.on_end(
            event_type="tool_end",
            data=data,
            run_id=str(run_id),
            invocation_id=invocation_id,
            session_id=session_id,
        )

        if pair:
            await self._emit_pair(pair)


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _build_llm_start_data(
    model: str,
    messages: list[ChatMessage],
    metadata: CallbackMetadata | None,
) -> ChatModelStartData:
    """Build a ``ChatModelStartData`` payload shared by both LLM start callbacks.

    Args:
        model: Model name or identifier.
        messages: Flattened list of chat messages.
        metadata: LangChain/LangGraph callback metadata, or ``None``.

    Returns:
        Typed event data dict for a ``chat_model_start`` event.
    """
    return ChatModelStartData(
        model=model,
        messages=messages,
        metadata=metadata,
    )


def _extract_metadata(
    kwargs: dict[str, Any],
) -> CallbackMetadata | None:
    """Extract callback metadata from LangChain kwargs.

    LangGraph injects context like ``langgraph_step``, ``langgraph_node``,
    etc. into the ``metadata`` kwarg on start callbacks.

    Args:
        kwargs: Callback keyword arguments.

    Returns:
        Metadata dict if present, or ``None``.
    """
    raw = kwargs.get("metadata")

    if raw is None:
        return None

    return cast(CallbackMetadata, raw)


def extract_model_name(serialized: dict[str, Any] | None) -> str:
    """Extract model name from LangChain serialized metadata.

    Args:
        serialized: Serialized configuration dict, or None.

    Returns:
        Model name string, or ``"unknown"`` if not found.
    """
    if serialized is None:
        return "unknown"

    if "name" in serialized:
        return str(serialized["name"])

    if "id" in serialized:
        id_val = serialized["id"]

        if isinstance(id_val, list) and id_val:
            return str(cast(object, id_val[-1]))

    kwargs = serialized.get("kwargs")

    if isinstance(kwargs, dict):
        kw = cast(dict[str, str], kwargs)

        return kw.get("model_name", "unknown")

    return "unknown"
