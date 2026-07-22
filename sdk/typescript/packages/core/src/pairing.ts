// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { randomUUID } from "node:crypto";
import type { AgentContext, PairedEvent, ParentContext } from "./format/types.js";
import type { CallbackMetadata, ChatModelStartData, LlmEndData, ToolEndData, ToolStartData } from "./types.js";

interface StartEventRecord {
  eventType: "chat_model_start" | "tool_start";
  data: ChatModelStartData | ToolStartData;
  agentId: string;
  parentRunId: string;
  parent: ParentContext | null;
  metadata: CallbackMetadata | null;
  agentContext: AgentContext | null;
}

export class EventPairBuffer {
  private pending = new Map<string, StartEventRecord>();

  onStart(args: {
    eventType: "chat_model_start" | "tool_start";
    data: ChatModelStartData | ToolStartData;
    runId: string;
    agentId: string;
    parent: ParentContext | null;
    metadata: CallbackMetadata | null;
    agentContext?: AgentContext | null;
    parentRunId?: string;
  }): void {
    this.pending.set(args.runId, {
      eventType: args.eventType,
      data: args.data,
      agentId: args.agentId,
      parentRunId: args.parentRunId ?? "",
      parent: args.parent,
      metadata: args.metadata,
      agentContext: args.agentContext ?? null,
    });
  }

  onEnd(args: {
    eventType: "llm_end" | "tool_end";
    data: LlmEndData | ToolEndData;
    runId: string;
    invocationId: string;
    sessionId: string;
  }): PairedEvent | null {
    const start = this.pending.get(args.runId);
    this.pending.delete(args.runId);
    if (!start) return null;
    if (args.eventType === "llm_end" && start.eventType === "chat_model_start") {
      return this.assembleLlmPair(start, args.data as LlmEndData, args.runId, args.invocationId, args.sessionId);
    }
    if (args.eventType === "tool_end" && start.eventType === "tool_start") {
      return this.assembleToolPair(start, args.data as ToolEndData, args.runId, args.invocationId, args.sessionId);
    }
    return null;
  }

  private assembleLlmPair(start: StartEventRecord, endData: LlmEndData, runId: string, invocationId: string, sessionId: string): PairedEvent {
    const startData = start.data as ChatModelStartData;
    const messages = startData.messages ?? [];
    const systemPrompt = messages.find((msg) => msg.role === "system")?.content ?? "";
    const userInstruction = [...messages].reverse().find((msg) => msg.role === "human" || msg.role === "user")?.content ?? "";
    return {
      eventId: randomUUID(),
      invocationId,
      sessionId,
      runId,
      parentRunId: start.parentRunId,
      timestamp: new Date().toISOString(),
      pairType: "llm",
      agent: { agentId: start.agentId, systemPrompt, userInstruction },
      parent: start.parent,
      data: {
        kind: "llm",
        model: startData.model ?? "unknown",
        messages,
        output: endData.output ?? "",
        toolCalls: endData.toolCalls ?? [],
        usage: endData.usage ?? null,
        error: endData.error,
      },
      metadata: start.metadata,
    };
  }

  private assembleToolPair(start: StartEventRecord, endData: ToolEndData, runId: string, invocationId: string, sessionId: string): PairedEvent {
    const startData = start.data as ToolStartData;
    return {
      eventId: randomUUID(),
      invocationId,
      sessionId,
      runId,
      parentRunId: start.parentRunId,
      timestamp: new Date().toISOString(),
      pairType: "tool",
      agent: start.agentContext ?? { agentId: start.agentId, systemPrompt: "", userInstruction: "" },
      parent: start.parent,
      data: {
        kind: "tool",
        toolName: startData.toolName ?? "unknown",
        toolCallId: startData.toolCallId ?? null,
        input: startData.input ?? "",
        output: endData.output ?? "",
        error: endData.error,
      },
      metadata: start.metadata,
    };
  }
}
