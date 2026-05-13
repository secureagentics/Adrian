"""Adrian unified event format types.

Each LangChain callback pair (chat_model_start + llm_end, or tool_start +
tool_end) is combined into a single ``PairedEvent`` that carries the full
context needed for classification: agent identity, parent agent context,
system prompt, user instruction, and the paired LLM or tool data.

This is the core data model that the SDK emits through registered handlers.
The worker core API, JSONL writer, and any custom handler all consume
``PairedEvent`` instances.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from adrian.types import ChatMessage, TokenUsage, ToolCallRecord


@dataclass(slots=True)
class AgentContext:
    """Identity and prompt context for the agent that produced an event.

    Attributes:
        agent_id: Opaque string identifying the agent. Derived from
            framework metadata (e.g. LangGraph checkpoint_ns path) or
            system prompt hash as fallback.
        system_prompt: The agent's system message, or empty string if
            no system message was present.
        user_instruction: The last human/user message the agent received,
            or empty string if none.
    """

    agent_id: str
    system_prompt: str = ""
    user_instruction: str = ""


@dataclass(slots=True)
class ParentContext:
    """Context from the parent agent that delegated to a sub-agent.

    Same shape as ``AgentContext`` but represents the parent in a
    delegation chain. Only populated when the event comes from a
    sub-agent; ``None`` for top-level agent events.

    Attributes:
        agent_id: Parent agent's identifier.
        system_prompt: Parent agent's system message.
        user_instruction: The instruction the parent was working on.
    """

    agent_id: str
    system_prompt: str = ""
    user_instruction: str = ""


@dataclass(slots=True)
class LlmPairData:
    """Combined data from a chat_model_start + llm_end pair.

    Represents one LLM decision point: what the model was given
    (messages) and what it decided to do (output, tool_calls).

    Attributes:
        model: Model name or identifier.
        messages: Full message list from chat_model_start.
        output: Generated text output from llm_end.
        tool_calls: Tool calls requested by the model.
        usage: Token usage statistics, or ``None``.
    """

    model: str
    messages: list[ChatMessage] = field(default_factory=list)
    output: str = ""
    tool_calls: list[ToolCallRecord] = field(default_factory=list)
    usage: TokenUsage | None = None


@dataclass(slots=True)
class ToolPairData:
    """Combined data from a tool_start + tool_end pair.

    Represents one tool execution: what was called and what it returned.

    Attributes:
        tool_name: Name of the tool.
        tool_call_id: Provider-assigned call identifier, or ``None``.
        input: Serialised tool input string.
        output: Tool execution result string.
    """

    tool_name: str
    tool_call_id: str | None = None
    input: str = ""
    output: str = ""


@dataclass(slots=True)
class PairedEvent:
    """A single paired event with full agent context.

    This is the core unit emitted by the SDK. Each instance combines a
    start+end callback pair, the agent's identity and prompt context,
    optional parent context (for sub-agents), and correlation IDs.

    Attributes:
        event_id: Unique identifier for this paired event.
        invocation_id: Correlation ID spanning the entire user prompt
            through all sub-agent execution.
        session_id: Long-lived session identifier.
        run_id: LangChain run_id that linked the start+end events.
        parent_run_id: LangChain parent_run_id.  For tool pairs this is
            the producing LLM's ``run_id``, the key block-mode uses to
            correlate a tool to the verdict of the LLM that requested it.
            Empty string when the pair has no parent in the run tree.
        timestamp: ISO 8601 timestamp of the end event.
        pair_type: ``"llm"`` for chat_model_start + llm_end, or
            ``"tool"`` for tool_start + tool_end.
        agent: Identity and prompt context of the producing agent.
        parent: Parent agent context if this is a sub-agent event,
            or ``None`` for top-level agents.
        data: The paired event data (``LlmPairData`` or ``ToolPairData``).
        metadata: Raw framework callback metadata, or ``None``.
    """

    event_id: str
    invocation_id: str
    session_id: str
    run_id: str
    timestamp: str
    pair_type: str
    agent: AgentContext
    parent: ParentContext | None
    data: LlmPairData | ToolPairData
    parent_run_id: str = ""
    metadata: dict[str, Any] | None = None
