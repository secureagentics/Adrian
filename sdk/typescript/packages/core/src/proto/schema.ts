// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import protobuf from "protobufjs";
import type { PairedEvent } from "../format/types.js";
import type { McpServer } from "../types.js";

export const SCHEMA_VERSION = 2;

export enum PairTypeProto {
  PAIR_TYPE_UNSPECIFIED = 0,
  PAIR_TYPE_LLM = 1,
  PAIR_TYPE_TOOL = 2,
}

export enum Mode {
  MODE_UNSPECIFIED = 0,
  MODE_ALERT = 1,
  MODE_HITL = 2,
  MODE_BLOCK = 3,
}

export interface PolicySnapshot {
  mode: Mode;
  policyM0: boolean;
  policyM2: boolean;
  policyM3: boolean;
  policyM4: boolean;
}

export interface HitlResponse {
  continueExecution: boolean;
}

export interface Verdict {
  eventId: string;
  sessionId: string;
  madCode: string;
  policy: PolicySnapshot;
  hitl: HitlResponse | null;
}

export interface LoginAck {
  policy: PolicySnapshot;
}

export type ClientFrame =
  | { login: { sessionId: string; llmStack: { provider: string; model: string }; schemaVersion: number } }
  | { pairedBatch: { events: PairedEvent[] } }
  | { mcpInventory: { servers: McpServer[] } };

export type ServerFrame =
  | { loginAck: LoginAck }
  | { verdict: Verdict };

const protoSource = `
syntax = "proto3";
package adrian.core_api.v1;
enum PairType { PAIR_TYPE_UNSPECIFIED = 0; PAIR_TYPE_LLM = 1; PAIR_TYPE_TOOL = 2; }
message ChatMessage { string role = 1; string content = 2; }
message ToolCall { string name = 1; string args = 2; string id = 3; }
message TokenUsage { int32 prompt_tokens = 1; int32 completion_tokens = 2; int32 total_tokens = 3; }
message AgentContext { string agent_id = 1; string system_prompt = 2; string user_instruction = 3; }
message LlmPairData { string model = 1; repeated ChatMessage messages = 2; string output = 3; repeated ToolCall tool_calls = 4; TokenUsage usage = 5; }
message ToolPairData { string tool_name = 1; string tool_call_id = 2; string input = 3; string output = 4; }
message PairedEvent { string event_id = 1; string invocation_id = 2; string session_id = 3; string run_id = 4; string parent_run_id = 5; string timestamp = 6; PairType pair_type = 7; AgentContext agent = 8; AgentContext parent = 9; oneof data { LlmPairData llm = 10; ToolPairData tool = 11; } bytes metadata_json = 20; }
message PairedEventBatch { repeated PairedEvent events = 1; }
message McpServer { string name = 1; string transport = 2; string endpoint = 3; }
message McpInventory { repeated McpServer servers = 1; }
message LLMStack { string provider = 1; string model = 2; }
message SessionLogin { string session_id = 1; LLMStack llm_stack = 2; reserved 3; uint32 schema_version = 4; }
message ClientFrame { reserved 2; oneof frame { SessionLogin login = 1; PairedEventBatch paired_batch = 3; McpInventory mcp_inventory = 4; } }
enum Mode { MODE_UNSPECIFIED = 0; MODE_ALERT = 1; MODE_HITL = 2; MODE_BLOCK = 3; }
message PolicySnapshot { Mode mode = 1; bool policy_m0 = 2; bool policy_m2 = 3; bool policy_m3 = 4; bool policy_m4 = 5; }
message HitlResponse { bool continue_execution = 1; }
message LoginAck { PolicySnapshot policy = 1; }
message ServerFrame { oneof frame { LoginAck login_ack = 1; Verdict verdict = 2; } }
message Verdict { string event_id = 1; string session_id = 2; reserved 3; string mad_code = 4; reserved 5; PolicySnapshot policy = 6; HitlResponse hitl = 7; }
`;

const root = protobuf.parse(protoSource, { keepCase: true }).root;
const ClientFrameType = root.lookupType("adrian.core_api.v1.ClientFrame");
const ServerFrameType = root.lookupType("adrian.core_api.v1.ServerFrame");

export function encodeClientFrame(frame: ClientFrame): Uint8Array {
  const message = toProtoClientFrame(frame);
  const err = ClientFrameType.verify(message);
  if (err) throw new Error(err);
  return ClientFrameType.encode(ClientFrameType.create(message)).finish();
}

export function decodeServerFrame(bytes: Uint8Array): ServerFrame {
  const decoded = ServerFrameType.toObject(ServerFrameType.decode(bytes), { defaults: true, bytes: Uint8Array }) as Record<string, unknown>;
  if (decoded.login_ack) return { loginAck: { policy: fromProtoPolicy((decoded.login_ack as Record<string, unknown>).policy as Record<string, unknown>) } };
  if (decoded.verdict) return { verdict: fromProtoVerdict(decoded.verdict as Record<string, unknown>) };
  throw new Error("server frame did not contain login_ack or verdict");
}

export function pairedEventToProto(event: PairedEvent): Record<string, unknown> {
  const base: Record<string, unknown> = {
    event_id: event.eventId,
    invocation_id: event.invocationId,
    session_id: event.sessionId,
    run_id: event.runId,
    parent_run_id: event.parentRunId,
    timestamp: event.timestamp,
    pair_type: event.pairType === "llm" ? PairTypeProto.PAIR_TYPE_LLM : PairTypeProto.PAIR_TYPE_TOOL,
    agent: agentToProto(event.agent),
    parent: event.parent ? agentToProto(event.parent) : { agent_id: "", system_prompt: "", user_instruction: "" },
  };
  if (event.data.kind === "llm") {
    base.llm = {
      model: event.data.model,
      messages: event.data.messages,
      output: event.data.output,
      tool_calls: event.data.toolCalls.map((call) => ({ name: call.name, args: JSON.stringify(call.args), id: call.id })),
      usage: event.data.usage ? {
        prompt_tokens: event.data.usage.promptTokens,
        completion_tokens: event.data.usage.completionTokens,
        total_tokens: event.data.usage.totalTokens,
      } : undefined,
    };
  } else {
    base.tool = { tool_name: event.data.toolName, tool_call_id: event.data.toolCallId ?? "", input: event.data.input, output: event.data.output };
  }
  if (event.metadata) base.metadata_json = new TextEncoder().encode(JSON.stringify(event.metadata));
  return base;
}

function toProtoClientFrame(frame: ClientFrame): Record<string, unknown> {
  if ("login" in frame) {
    return { login: { session_id: frame.login.sessionId, llm_stack: frame.login.llmStack, schema_version: frame.login.schemaVersion } };
  }
  if ("pairedBatch" in frame) {
    return { paired_batch: { events: frame.pairedBatch.events.map(pairedEventToProto) } };
  }
  return { mcp_inventory: { servers: frame.mcpInventory.servers } };
}

function agentToProto(agent: { agentId: string; systemPrompt: string; userInstruction: string }): Record<string, unknown> {
  return { agent_id: agent.agentId, system_prompt: agent.systemPrompt, user_instruction: agent.userInstruction };
}

function fromProtoPolicy(policyRaw: Record<string, unknown> | undefined): PolicySnapshot {
  const policy = policyRaw ?? {};
  return {
    mode: Number(policy.mode ?? 0) as Mode,
    policyM0: Boolean(policy.policy_m0),
    policyM2: Boolean(policy.policy_m2),
    policyM3: Boolean(policy.policy_m3),
    policyM4: Boolean(policy.policy_m4),
  };
}

function fromProtoVerdict(raw: Record<string, unknown>): Verdict {
  const hitl = raw.hitl as Record<string, unknown> | undefined;
  return {
    eventId: String(raw.event_id ?? ""),
    sessionId: String(raw.session_id ?? ""),
    madCode: String(raw.mad_code ?? ""),
    policy: fromProtoPolicy(raw.policy as Record<string, unknown> | undefined),
    hitl: hitl ? { continueExecution: Boolean(hitl.continue_execution) } : null,
  };
}
