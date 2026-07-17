from .buf.validate import validate_pb2 as _validate_pb2
from google.protobuf.internal import containers as _containers
from google.protobuf.internal import enum_type_wrapper as _enum_type_wrapper
from google.protobuf import descriptor as _descriptor
from google.protobuf import message as _message
from typing import ClassVar as _ClassVar, Iterable as _Iterable, Mapping as _Mapping, Optional as _Optional, Union as _Union

DESCRIPTOR: _descriptor.FileDescriptor

class PairType(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    PAIR_TYPE_UNSPECIFIED: _ClassVar[PairType]
    PAIR_TYPE_LLM: _ClassVar[PairType]
    PAIR_TYPE_TOOL: _ClassVar[PairType]

class Mode(int, metaclass=_enum_type_wrapper.EnumTypeWrapper):
    __slots__ = ()
    MODE_UNSPECIFIED: _ClassVar[Mode]
    MODE_ALERT: _ClassVar[Mode]
    MODE_HITL: _ClassVar[Mode]
    MODE_BLOCK: _ClassVar[Mode]
PAIR_TYPE_UNSPECIFIED: PairType
PAIR_TYPE_LLM: PairType
PAIR_TYPE_TOOL: PairType
MODE_UNSPECIFIED: Mode
MODE_ALERT: Mode
MODE_HITL: Mode
MODE_BLOCK: Mode

class ChatMessage(_message.Message):
    __slots__ = ("role", "content")
    ROLE_FIELD_NUMBER: _ClassVar[int]
    CONTENT_FIELD_NUMBER: _ClassVar[int]
    role: str
    content: str
    def __init__(self, role: _Optional[str] = ..., content: _Optional[str] = ...) -> None: ...

class ToolCall(_message.Message):
    __slots__ = ("name", "args", "id")
    NAME_FIELD_NUMBER: _ClassVar[int]
    ARGS_FIELD_NUMBER: _ClassVar[int]
    ID_FIELD_NUMBER: _ClassVar[int]
    name: str
    args: str
    id: str
    def __init__(self, name: _Optional[str] = ..., args: _Optional[str] = ..., id: _Optional[str] = ...) -> None: ...

class TokenUsage(_message.Message):
    __slots__ = ("prompt_tokens", "completion_tokens", "total_tokens")
    PROMPT_TOKENS_FIELD_NUMBER: _ClassVar[int]
    COMPLETION_TOKENS_FIELD_NUMBER: _ClassVar[int]
    TOTAL_TOKENS_FIELD_NUMBER: _ClassVar[int]
    prompt_tokens: int
    completion_tokens: int
    total_tokens: int
    def __init__(self, prompt_tokens: _Optional[int] = ..., completion_tokens: _Optional[int] = ..., total_tokens: _Optional[int] = ...) -> None: ...

class AgentContext(_message.Message):
    __slots__ = ("agent_id", "system_prompt", "user_instruction")
    AGENT_ID_FIELD_NUMBER: _ClassVar[int]
    SYSTEM_PROMPT_FIELD_NUMBER: _ClassVar[int]
    USER_INSTRUCTION_FIELD_NUMBER: _ClassVar[int]
    agent_id: str
    system_prompt: str
    user_instruction: str
    def __init__(self, agent_id: _Optional[str] = ..., system_prompt: _Optional[str] = ..., user_instruction: _Optional[str] = ...) -> None: ...

class LlmPairData(_message.Message):
    __slots__ = ("model", "messages", "output", "tool_calls", "usage")
    MODEL_FIELD_NUMBER: _ClassVar[int]
    MESSAGES_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALLS_FIELD_NUMBER: _ClassVar[int]
    USAGE_FIELD_NUMBER: _ClassVar[int]
    model: str
    messages: _containers.RepeatedCompositeFieldContainer[ChatMessage]
    output: str
    tool_calls: _containers.RepeatedCompositeFieldContainer[ToolCall]
    usage: TokenUsage
    def __init__(self, model: _Optional[str] = ..., messages: _Optional[_Iterable[_Union[ChatMessage, _Mapping]]] = ..., output: _Optional[str] = ..., tool_calls: _Optional[_Iterable[_Union[ToolCall, _Mapping]]] = ..., usage: _Optional[_Union[TokenUsage, _Mapping]] = ...) -> None: ...

class ToolPairData(_message.Message):
    __slots__ = ("tool_name", "tool_call_id", "input", "output")
    TOOL_NAME_FIELD_NUMBER: _ClassVar[int]
    TOOL_CALL_ID_FIELD_NUMBER: _ClassVar[int]
    INPUT_FIELD_NUMBER: _ClassVar[int]
    OUTPUT_FIELD_NUMBER: _ClassVar[int]
    tool_name: str
    tool_call_id: str
    input: str
    output: str
    def __init__(self, tool_name: _Optional[str] = ..., tool_call_id: _Optional[str] = ..., input: _Optional[str] = ..., output: _Optional[str] = ...) -> None: ...

class PairedEvent(_message.Message):
    __slots__ = ("event_id", "invocation_id", "session_id", "run_id", "parent_run_id", "timestamp", "pair_type", "agent", "parent", "llm", "tool", "metadata_json", "connection_id", "source")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    INVOCATION_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    RUN_ID_FIELD_NUMBER: _ClassVar[int]
    PARENT_RUN_ID_FIELD_NUMBER: _ClassVar[int]
    TIMESTAMP_FIELD_NUMBER: _ClassVar[int]
    PAIR_TYPE_FIELD_NUMBER: _ClassVar[int]
    AGENT_FIELD_NUMBER: _ClassVar[int]
    PARENT_FIELD_NUMBER: _ClassVar[int]
    LLM_FIELD_NUMBER: _ClassVar[int]
    TOOL_FIELD_NUMBER: _ClassVar[int]
    METADATA_JSON_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_ID_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    invocation_id: str
    session_id: str
    run_id: str
    parent_run_id: str
    timestamp: str
    pair_type: PairType
    agent: AgentContext
    parent: AgentContext
    llm: LlmPairData
    tool: ToolPairData
    metadata_json: bytes
    connection_id: str
    source: str
    def __init__(self, event_id: _Optional[str] = ..., invocation_id: _Optional[str] = ..., session_id: _Optional[str] = ..., run_id: _Optional[str] = ..., parent_run_id: _Optional[str] = ..., timestamp: _Optional[str] = ..., pair_type: _Optional[_Union[PairType, str]] = ..., agent: _Optional[_Union[AgentContext, _Mapping]] = ..., parent: _Optional[_Union[AgentContext, _Mapping]] = ..., llm: _Optional[_Union[LlmPairData, _Mapping]] = ..., tool: _Optional[_Union[ToolPairData, _Mapping]] = ..., metadata_json: _Optional[bytes] = ..., connection_id: _Optional[str] = ..., source: _Optional[str] = ...) -> None: ...

class PairedEventBatch(_message.Message):
    __slots__ = ("events",)
    EVENTS_FIELD_NUMBER: _ClassVar[int]
    events: _containers.RepeatedCompositeFieldContainer[PairedEvent]
    def __init__(self, events: _Optional[_Iterable[_Union[PairedEvent, _Mapping]]] = ...) -> None: ...

class McpServer(_message.Message):
    __slots__ = ("name", "transport", "endpoint")
    NAME_FIELD_NUMBER: _ClassVar[int]
    TRANSPORT_FIELD_NUMBER: _ClassVar[int]
    ENDPOINT_FIELD_NUMBER: _ClassVar[int]
    name: str
    transport: str
    endpoint: str
    def __init__(self, name: _Optional[str] = ..., transport: _Optional[str] = ..., endpoint: _Optional[str] = ...) -> None: ...

class McpInventory(_message.Message):
    __slots__ = ("servers",)
    SERVERS_FIELD_NUMBER: _ClassVar[int]
    servers: _containers.RepeatedCompositeFieldContainer[McpServer]
    def __init__(self, servers: _Optional[_Iterable[_Union[McpServer, _Mapping]]] = ...) -> None: ...

class LLMStack(_message.Message):
    __slots__ = ("provider", "model")
    PROVIDER_FIELD_NUMBER: _ClassVar[int]
    MODEL_FIELD_NUMBER: _ClassVar[int]
    provider: str
    model: str
    def __init__(self, provider: _Optional[str] = ..., model: _Optional[str] = ...) -> None: ...

class SessionLogin(_message.Message):
    __slots__ = ("session_id", "llm_stack", "schema_version", "source", "connection_id")
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    LLM_STACK_FIELD_NUMBER: _ClassVar[int]
    SCHEMA_VERSION_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    CONNECTION_ID_FIELD_NUMBER: _ClassVar[int]
    session_id: str
    llm_stack: LLMStack
    schema_version: int
    source: str
    connection_id: str
    def __init__(self, session_id: _Optional[str] = ..., llm_stack: _Optional[_Union[LLMStack, _Mapping]] = ..., schema_version: _Optional[int] = ..., source: _Optional[str] = ..., connection_id: _Optional[str] = ...) -> None: ...

class ClientFrame(_message.Message):
    __slots__ = ("login", "paired_batch", "mcp_inventory")
    LOGIN_FIELD_NUMBER: _ClassVar[int]
    PAIRED_BATCH_FIELD_NUMBER: _ClassVar[int]
    MCP_INVENTORY_FIELD_NUMBER: _ClassVar[int]
    login: SessionLogin
    paired_batch: PairedEventBatch
    mcp_inventory: McpInventory
    def __init__(self, login: _Optional[_Union[SessionLogin, _Mapping]] = ..., paired_batch: _Optional[_Union[PairedEventBatch, _Mapping]] = ..., mcp_inventory: _Optional[_Union[McpInventory, _Mapping]] = ...) -> None: ...

class PolicySnapshot(_message.Message):
    __slots__ = ("mode", "policy_m0", "policy_m2", "policy_m3", "policy_m4")
    MODE_FIELD_NUMBER: _ClassVar[int]
    POLICY_M0_FIELD_NUMBER: _ClassVar[int]
    POLICY_M2_FIELD_NUMBER: _ClassVar[int]
    POLICY_M3_FIELD_NUMBER: _ClassVar[int]
    POLICY_M4_FIELD_NUMBER: _ClassVar[int]
    mode: Mode
    policy_m0: bool
    policy_m2: bool
    policy_m3: bool
    policy_m4: bool
    def __init__(self, mode: _Optional[_Union[Mode, str]] = ..., policy_m0: bool = ..., policy_m2: bool = ..., policy_m3: bool = ..., policy_m4: bool = ...) -> None: ...

class HitlResponse(_message.Message):
    __slots__ = ("continue_execution",)
    CONTINUE_EXECUTION_FIELD_NUMBER: _ClassVar[int]
    continue_execution: bool
    def __init__(self, continue_execution: bool = ...) -> None: ...

class LoginAck(_message.Message):
    __slots__ = ("policy", "source")
    POLICY_FIELD_NUMBER: _ClassVar[int]
    SOURCE_FIELD_NUMBER: _ClassVar[int]
    policy: PolicySnapshot
    source: str
    def __init__(self, policy: _Optional[_Union[PolicySnapshot, _Mapping]] = ..., source: _Optional[str] = ...) -> None: ...

class ServerFrame(_message.Message):
    __slots__ = ("login_ack", "verdict")
    LOGIN_ACK_FIELD_NUMBER: _ClassVar[int]
    VERDICT_FIELD_NUMBER: _ClassVar[int]
    login_ack: LoginAck
    verdict: Verdict
    def __init__(self, login_ack: _Optional[_Union[LoginAck, _Mapping]] = ..., verdict: _Optional[_Union[Verdict, _Mapping]] = ...) -> None: ...

class Verdict(_message.Message):
    __slots__ = ("event_id", "session_id", "mad_code", "policy", "hitl")
    EVENT_ID_FIELD_NUMBER: _ClassVar[int]
    SESSION_ID_FIELD_NUMBER: _ClassVar[int]
    MAD_CODE_FIELD_NUMBER: _ClassVar[int]
    POLICY_FIELD_NUMBER: _ClassVar[int]
    HITL_FIELD_NUMBER: _ClassVar[int]
    event_id: str
    session_id: str
    mad_code: str
    policy: PolicySnapshot
    hitl: HitlResponse
    def __init__(self, event_id: _Optional[str] = ..., session_id: _Optional[str] = ..., mad_code: _Optional[str] = ..., policy: _Optional[_Union[PolicySnapshot, _Mapping]] = ..., hitl: _Optional[_Union[HitlResponse, _Mapping]] = ...) -> None: ...
