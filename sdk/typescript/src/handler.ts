import { currentConfig, type AdrianConfig } from "./config.js";
import { getInvocationId } from "./context.js";
import { AgentContextTracker } from "./context.js";
import type { PairedEvent } from "./format/types.js";
import { HookRegistry } from "./hooks.js";
import { deriveAgentId } from "./identity.js";
import { EventPairBuffer } from "./pairing.js";
import type { CallbackMetadata, ChatMessage, EventData, EventRecord, LlmEndData, ToolCallRecord, ToolEndData, VerdictContext } from "./types.js";
import type { Verdict } from "./proto/schema.js";

export interface AdrianCallbackHandlerOptions {
  pairBuffer: EventPairBuffer;
  contextTracker: AgentContextTracker;
  hooks: HookRegistry;
  config: AdrianConfig;
}

export class AdrianCallbackHandler {
  name = "AdrianCallbackHandler";
  private pairBuffer: EventPairBuffer;
  private contextTracker: AgentContextTracker;
  private hooks: HookRegistry;
  private config: AdrianConfig;
  private eventMap = new Map<string, EventRecord>();
  private currentAgentId = "default";

  constructor(options: AdrianCallbackHandlerOptions) {
    this.pairBuffer = options.pairBuffer;
    this.contextTracker = options.contextTracker;
    this.hooks = options.hooks;
    this.config = options.config;
  }

  async handleChatModelStart(llm: Record<string, unknown>, messages: unknown[][], runId: string, parentRunId?: string, extraParams?: Record<string, unknown>): Promise<void> {
    const flatMessages = messages.flat().map(messageToChatMessage);
    const metadata = extractMetadata(extraParams);
    const agentId = deriveAgentId(metadata, flatMessages);
    this.currentAgentId = agentId;
    const systemPrompt = flatMessages.find((msg) => msg.role === "system")?.content ?? "";
    const userInstruction = [...flatMessages].reverse().find((msg) => msg.role === "human" || msg.role === "user")?.content ?? "";
    const parent = this.contextTracker.update(agentId, systemPrompt, userInstruction);
    this.pairBuffer.onStart({
      eventType: "chat_model_start",
      data: { model: extractModelName(llm), messages: flatMessages, metadata },
      runId: String(runId),
      agentId,
      parent: parent ?? this.contextTracker.getParent(agentId),
      metadata,
      parentRunId: parentRunId ? String(parentRunId) : "",
    });
  }

  async handleLLMStart(llm: Record<string, unknown>, prompts: string[], runId: string, parentRunId?: string, extraParams?: Record<string, unknown>): Promise<void> {
    const flatMessages = prompts.map((content) => ({ role: "human", content }));
    const metadata = extractMetadata(extraParams);
    const agentId = deriveAgentId(metadata, flatMessages);
    this.currentAgentId = agentId;
    const parent = this.contextTracker.update(agentId, "", prompts[0] ?? "");
    this.pairBuffer.onStart({
      eventType: "chat_model_start",
      data: { model: extractModelName(llm), messages: flatMessages, metadata },
      runId: String(runId),
      agentId,
      parent: parent ?? this.contextTracker.getParent(agentId),
      metadata,
      parentRunId: parentRunId ? String(parentRunId) : "",
    });
  }

  async handleLLMEnd(output: unknown, runId: string): Promise<void> {
    const data = extractLlmEndData(output);
    const pair = this.pairBuffer.onEnd({
      eventType: "llm_end",
      data,
      runId: String(runId),
      invocationId: this.resolveInvocationId(),
      sessionId: this.resolveSessionId(),
    });
    if (pair) {
      if (data.toolCalls.length > 0) this.contextTracker.markDelegated(pair.agent.agentId);
      await this.emitPair(pair);
    }
  }

  async handleToolStart(tool: Record<string, unknown>, input: string, runId: string, parentRunId?: string, extraParams?: Record<string, unknown>): Promise<void> {
    const metadata = extractMetadata(extraParams);
    let agentId = this.currentAgentId;
    if (metadata) {
      const candidate = deriveAgentId(metadata);
      if (this.contextTracker.hasContext(candidate)) agentId = candidate;
    }
    this.pairBuffer.onStart({
      eventType: "tool_start",
      data: {
        toolName: String(tool.name ?? tool.id ?? "unknown"),
        toolCallId: typeof extraParams?.tool_call_id === "string" ? extraParams.tool_call_id : typeof extraParams?.toolCallId === "string" ? extraParams.toolCallId : null,
        input: String(input ?? ""),
        metadata,
      },
      runId: String(runId),
      agentId,
      parent: this.contextTracker.getParent(agentId),
      metadata,
      agentContext: this.contextTracker.getContext(agentId),
      parentRunId: parentRunId ? String(parentRunId) : "",
    });
  }

  async handleToolEnd(output: unknown, runId: string): Promise<void> {
    const data: ToolEndData = { output: stringifyOutput(output) };
    const pair = this.pairBuffer.onEnd({
      eventType: "tool_end",
      data,
      runId: String(runId),
      invocationId: this.resolveInvocationId(),
      sessionId: this.resolveSessionId(),
    });
    if (pair) await this.emitPair(pair);
  }

  async handleVerdict(verdict: Verdict): Promise<void> {
    const record = this.eventMap.get(verdict.eventId);
    this.eventMap.delete(verdict.eventId);
    if (!record) return;
    const ctx: VerdictContext = {
      eventId: verdict.eventId,
      sessionId: verdict.sessionId,
      eventType: record.eventType,
      eventData: record.data,
      runId: record.runId,
      parentRunId: record.parentRunId,
      policy: verdict.policy,
      madCode: verdict.madCode,
      hitl: verdict.hitl,
    };
    await this.config.onVerdict?.(ctx);
    const prefix = verdict.madCode.slice(0, 2);
    if ((prefix === "M3" || prefix === "M4") && this.config.onBlock) await this.config.onBlock(ctx);
    if (prefix === "M2" && this.config.onAudit) await this.config.onAudit(ctx);
  }

  private async emitPair(pair: PairedEvent): Promise<void> {
    await this.hooks.emit(pair);
    this.eventMap.set(pair.eventId, {
      eventType: pair.pairType,
      data: pair.data as unknown as EventData,
      runId: pair.runId,
      parentRunId: pair.parentRunId || null,
    });
    await this.config.onEvent?.(pair.pairType, pair.data as unknown as EventData, pair.runId, pair.parentRunId || null, pair.eventId);
  }

  private resolveSessionId(): string {
    const cfg = currentConfig() ?? this.config;
    if (!cfg.sessionId) throw new Error("session_id is not set, init() must be called before capturing events");
    return cfg.sessionId;
  }

  private resolveInvocationId(): string {
    return getInvocationId() ?? "no_invocation";
  }
}

export function extractModelName(serialized: Record<string, unknown> | null | undefined): string {
  if (!serialized) return "unknown";
  if (typeof serialized.name === "string") return serialized.name;
  if (Array.isArray(serialized.id) && serialized.id.length > 0) return String(serialized.id.at(-1));
  const kwargs = serialized.kwargs;
  if (kwargs && typeof kwargs === "object" && "model_name" in kwargs) return String((kwargs as Record<string, unknown>).model_name);
  return "unknown";
}

function extractMetadata(extraParams?: Record<string, unknown>): CallbackMetadata | null {
  const raw = extraParams?.metadata;
  if (raw === null || raw === undefined || typeof raw !== "object" || Array.isArray(raw)) return null;
  return raw as CallbackMetadata;
}

function messageToChatMessage(message: unknown): ChatMessage {
  const obj = message && typeof message === "object" ? message as Record<string, unknown> : {};
  const role = String(obj.type ?? obj.role ?? "unknown");
  const content = obj.content;
  return { role, content: typeof content === "string" ? content : JSON.stringify(content ?? "") };
}

function extractLlmEndData(output: unknown): LlmEndData {
  const generations = (output as { generations?: unknown[][] })?.generations ?? [];
  const first = generations[0]?.[0] as Record<string, unknown> | undefined;
  const text = typeof first?.text === "string" ? first.text : "";
  const message = first?.message as Record<string, unknown> | undefined;
  const rawCalls = Array.isArray(message?.tool_calls) ? message.tool_calls : Array.isArray(message?.toolCalls) ? message.toolCalls : [];
  const toolCalls: ToolCallRecord[] = rawCalls.map((call) => {
    const obj = call && typeof call === "object" ? call as Record<string, unknown> : {};
    return { id: String(obj.id ?? ""), name: String(obj.name ?? ""), args: (obj.args && typeof obj.args === "object" ? obj.args : {}) as ToolCallRecord["args"] };
  });
  const llmOutput = (output as { llmOutput?: Record<string, unknown>; llm_output?: Record<string, unknown> })?.llmOutput ?? (output as { llm_output?: Record<string, unknown> })?.llm_output ?? {};
  const usageRaw = llmOutput.tokenUsage ?? llmOutput.token_usage;
  const usageObj = usageRaw && typeof usageRaw === "object" ? usageRaw as Record<string, unknown> : null;
  return {
    output: text,
    toolCalls,
    usage: usageObj ? {
      promptTokens: Number(usageObj.promptTokens ?? usageObj.prompt_tokens ?? 0),
      completionTokens: Number(usageObj.completionTokens ?? usageObj.completion_tokens ?? 0),
      totalTokens: Number(usageObj.totalTokens ?? usageObj.total_tokens ?? 0),
    } : null,
  };
}

function stringifyOutput(output: unknown): string {
  if (typeof output === "string") return output;
  try {
    return JSON.stringify(output);
  } catch {
    return String(output);
  }
}
