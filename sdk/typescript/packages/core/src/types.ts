import type { PairedEvent } from "./format/types.js";
import type { PolicySnapshot, HitlResponse } from "./proto/schema.js";

export type JsonPrimitive = string | number | boolean | null;
export type JsonValue = JsonPrimitive | JsonValue[] | { [key: string]: JsonValue };
export type MetadataValue = JsonPrimitive | string[];
export type CallbackMetadata = Record<string, MetadataValue>;
export type ToolArgs = Record<string, JsonValue>;

export interface ChatMessage {
  role: string;
  content: string;
}

export interface ToolCallRecord {
  id: string;
  name: string;
  args: ToolArgs;
}

export interface TokenUsage {
  promptTokens: number;
  completionTokens: number;
  totalTokens: number;
}

export interface ErrorData {
  name: string;
  message: string;
  stack?: string;
}

export interface ChatModelStartData {
  model: string;
  messages: ChatMessage[];
  metadata: CallbackMetadata | null;
}

export interface LlmStartData {
  model: string;
  prompts: string[];
  metadata: CallbackMetadata | null;
}

export interface LlmEndData {
  output: string;
  toolCalls: ToolCallRecord[];
  usage: TokenUsage | null;
  error?: ErrorData;
}

export interface ToolStartData {
  toolName: string;
  toolCallId: string | null;
  input: string;
  metadata: CallbackMetadata | null;
}

export interface ToolEndData {
  output: string;
  error?: ErrorData;
}

export type EventData = ChatModelStartData | LlmStartData | LlmEndData | ToolStartData | ToolEndData;

export interface EventRecord {
  eventType: string;
  data: EventData;
  runId: string;
  parentRunId: string | null;
}

export interface VerdictContext {
  eventId: string;
  sessionId: string;
  eventType: string;
  eventData: EventData;
  runId: string;
  parentRunId: string | null;
  policy: PolicySnapshot;
  madCode: string;
  hitl: HitlResponse | null;
}

export interface McpServer {
  name: string;
  transport: string;
  endpoint: string;
}

export interface EventHandler {
  onPairedEvent(event: PairedEvent): Promise<void> | void;
  close(): Promise<void> | void;
}
