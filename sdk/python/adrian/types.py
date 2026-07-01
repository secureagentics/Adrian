"""Typed event schema for Adrian SDK.

Defines TypedDict classes matching the Adrian protobuf schema.  These serve as
the Python-side contract for all event data flowing through the handler and
writer layers.  Using TypedDicts keeps the data JSON-serialisable (plain dicts
at runtime) while providing compile-time type safety via basedpyright.
"""

from dataclasses import dataclass
from typing import TypedDict

from adrian.proto import event_pb2 as pb

# ------------------------------------------------------------------
# Shared primitives
# ------------------------------------------------------------------

type JsonPrimitive = str | int | float | bool | None
"""A single JSON-safe scalar value."""

type MetadataValue = JsonPrimitive | list[str]
"""JSON-safe primitive for a single metadata entry."""

type CallbackMetadata = dict[str, MetadataValue]
"""Arbitrary key-value metadata injected by LangChain/LangGraph callbacks."""

type ToolArgs = dict[str, JsonPrimitive]
"""Tool call argument map (JSON primitive values only)."""


# ------------------------------------------------------------------
# Shared sub-messages
# ------------------------------------------------------------------


class ChatMessage(TypedDict):
    """A single chat message (role + content).

    Attributes:
        role: Message role (``"system"``, ``"human"``, ``"ai"``, ``"tool"``).
        content: Text content of the message.
    """

    role: str
    content: str


class ToolCallRecord(TypedDict):
    """Record of a single tool call requested by the model.

    Attributes:
        id: Provider-assigned tool call identifier.
        name: Name of the tool being called.
        args: Argument key-value pairs (JSON primitive values).
    """

    id: str
    name: str
    args: ToolArgs


class TokenUsage(TypedDict):
    """Token usage statistics from an LLM response.

    Attributes:
        prompt_tokens: Number of tokens in the prompt.
        completion_tokens: Number of tokens in the completion.
        total_tokens: Total token count.
    """

    prompt_tokens: int
    completion_tokens: int
    total_tokens: int


# ------------------------------------------------------------------
# Event data types (one per event_type)
# ------------------------------------------------------------------


class ChatModelStartData(TypedDict):
    """Data payload for ``chat_model_start`` events.

    Attributes:
        model: Model name or identifier.
        messages: Flattened list of chat messages sent to the model.
        metadata: LangChain/LangGraph callback metadata, or ``None``.
    """

    model: str
    messages: list[ChatMessage]
    metadata: CallbackMetadata | None


class LlmStartData(TypedDict):
    """Data payload for ``llm_start`` events (legacy text completions).

    Attributes:
        model: Model name or identifier.
        prompts: Raw text prompts sent to the model.
        metadata: LangChain/LangGraph callback metadata, or ``None``.
    """

    model: str
    prompts: list[str]
    metadata: CallbackMetadata | None


class LlmEndData(TypedDict):
    """Data payload for ``llm_end`` events.

    Attributes:
        output: Generated text output.
        tool_calls: Tool calls extracted from the response.
        usage: Token usage statistics, or ``None`` if unavailable.
    """

    output: str
    tool_calls: list[ToolCallRecord]
    usage: TokenUsage | None


class ToolStartData(TypedDict):
    """Data payload for ``tool_start`` events.

    Attributes:
        tool_name: Name of the tool being invoked.
        tool_call_id: Provider-assigned call identifier, or ``None``.
        input: Serialised tool input string.
        metadata: LangChain/LangGraph callback metadata, or ``None``.
    """

    tool_name: str
    tool_call_id: str | None
    input: str
    metadata: CallbackMetadata | None


class ToolEndData(TypedDict):
    """Data payload for ``tool_end`` events.

    Attributes:
        output: Tool execution result as a string.
    """

    output: str


# Union of all event data types
type EventData = (
    ChatModelStartData | LlmStartData | LlmEndData | ToolStartData | ToolEndData
)


# ------------------------------------------------------------------
# Event envelope
# ------------------------------------------------------------------


class AdrianEvent(TypedDict):
    """Complete event envelope written as one JSONL line.

    Attributes:
        timestamp: ISO 8601 timestamp string.
        event_type: Event type identifier.
        run_id: LangChain run ID (stringified UUID).
        parent_run_id: Parent run ID if nested, or ``None``.
        data: Event-specific payload.
    """

    timestamp: str
    event_type: str
    run_id: str
    parent_run_id: str | None
    data: EventData


# ------------------------------------------------------------------
# Event record (stored in handler event map for verdict enrichment)
# ------------------------------------------------------------------


@dataclass(slots=True)
class EventRecord:
    """Cached event metadata for enriching verdict callbacks.

    Stored in the handler's event map keyed by event_id.  Retrieved and
    removed when the corresponding verdict arrives.

    Attributes:
        event_type: SDK event type string (e.g. ``"llm_end"``).
        data: Original event payload TypedDict.
        run_id: LangChain run ID.
        parent_run_id: Parent run ID if nested, or ``None``.
    """

    event_type: str
    data: EventData
    run_id: str
    parent_run_id: str | None


# ------------------------------------------------------------------
# Verdict context (passed to all verdict callbacks)
# ------------------------------------------------------------------


@dataclass(slots=True)
class VerdictContext:
    """Enriched verdict context passed to all verdict callbacks.

    Combines the verdict metadata from the Worker Core API with the
    original event data from the SDK's event map.

    Attributes:
        event_id: The event ID that was classified.
        session_id: Session identifier.
        event_type: SDK event type of the classified event.
        event_data: Original event payload TypedDict.
        run_id: LangChain run ID.
        parent_run_id: Parent run ID if nested, or ``None``.
        status: Classifier result status. ``VERDICT_STATUS_ERROR`` means
            the classifier did not produce a MAD code; ``mad_code`` is empty.
        mad_code: MAD policy code the classifier returned on OK verdicts
            (e.g. ``"M0"``, ``"M2_C"``, ``"M4_a"``).  Empty string
            means no classifier-produced MAD code exists.
        policy: Org's effective execution-mode policy at the moment
            this verdict was decided.  Carries the mode (alert /
            block / hitl) and per-MAD-code scope booleans.
        hitl: Present only when this verdict represents a HITL
            review resolution from the dashboard.  ``None`` on
            auto-classified verdicts and on out-of-scope verdicts
            forwarded immediately.
    """

    event_id: str
    session_id: str
    event_type: str
    event_data: EventData
    run_id: str
    parent_run_id: str | None
    policy: pb.PolicySnapshot
    status: int = pb.VERDICT_STATUS_UNSPECIFIED
    mad_code: str = ""
    hitl: pb.HitlResponse | None = None


# ------------------------------------------------------------------
# MCP server identity (passed to on_mcp_server callback)
# ------------------------------------------------------------------


@dataclass(slots=True, frozen=True)
class McpServer:
    """An observed MCP server.

    Attributes:
        name: Server identifier.  For adapter-layer captures this is
            the key from ``MultiServerMCPClient.connections``; for
            raw-transport captures it is a synthesised
            ``"<transport>:<endpoint>"`` string.
        transport: One of ``"stdio"``, ``"sse"``, ``"streamable_http"``,
            ``"websocket"``, or ``"unknown"``.
        endpoint: The URL for SSE / HTTP / WebSocket transports, or
            the joined command line for stdio.  Empty string when
            neither is available.
    """

    name: str
    transport: str
    endpoint: str
