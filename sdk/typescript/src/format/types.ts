import type { CallbackMetadata, ChatMessage, TokenUsage, ToolCallRecord } from "../types.js";

export interface AgentContext {
  agentId: string;
  systemPrompt: string;
  userInstruction: string;
}

export interface ParentContext {
  agentId: string;
  systemPrompt: string;
  userInstruction: string;
}

export interface LlmPairData {
  kind: "llm";
  model: string;
  messages: ChatMessage[];
  output: string;
  toolCalls: ToolCallRecord[];
  usage: TokenUsage | null;
}

export interface ToolPairData {
  kind: "tool";
  toolName: string;
  toolCallId: string | null;
  input: string;
  output: string;
}

export type PairType = "llm" | "tool";
export type PairData = LlmPairData | ToolPairData;

export interface PairedEvent {
  eventId: string;
  invocationId: string;
  sessionId: string;
  runId: string;
  parentRunId: string;
  timestamp: string;
  pairType: PairType;
  agent: AgentContext;
  parent: ParentContext | null;
  data: PairData;
  metadata: CallbackMetadata | null;
}
