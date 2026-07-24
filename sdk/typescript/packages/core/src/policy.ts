// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { currentConfig } from "./config.js";
import { shouldHalt, type WebSocketClient } from "./ws.js";

/** Tool result content returned when tool execution is blocked by policy. */
export const BLOCKED_TOOL_MESSAGE = "[BLOCKED by security policy]";

export type GateToolCallsReason = "policy_halt" | "verdict_timeout";

export type GateToolCallsResult =
  | { action: "allow" }
  | { action: "block"; reason: GateToolCallsReason };

export class AdrianPolicyBlockedError extends Error {
  readonly reason: GateToolCallsReason;

  constructor(reason: GateToolCallsReason) {
    super(`Adrian security policy blocked execution (${reason})`);
    this.name = "AdrianPolicyBlockedError";
    this.reason = reason;
  }
}

/**
 * Waits for backend verdicts on tool calls proposed by a prior LLM turn.
 * No-ops when WebSocket is absent or policy mode is not BLOCK/HITL.
 */
export async function gateToolCallIds(
  toolCallIds: string[],
  ws: WebSocketClient | null,
  blockTimeoutSeconds?: number,
): Promise<GateToolCallsResult> {
  if (toolCallIds.length === 0) return { action: "allow" };
  if (!ws) return { action: "allow" };

  const timeoutSeconds = blockTimeoutSeconds ?? currentConfig()?.blockTimeout ?? 30;
  const policyReady = await ws.waitForPolicyReady(timeoutSeconds);
  if (!policyReady || !ws.policyActive()) return { action: "allow" };

  const correlatableIds = toolCallIds.filter((id) => id);
  if (correlatableIds.length === 0) return { action: "allow" };

  const verdictTimeout = ws.blockTimeout(timeoutSeconds);
  const verdicts = await Promise.all(correlatableIds.map((id) => ws.waitForToolCallVerdict(id, verdictTimeout)));
  if (verdicts.some((verdict) => verdict !== null && shouldHalt(verdict))) return { action: "block", reason: "policy_halt" };
  return { action: "allow" };
}

/** Throws {@link AdrianPolicyBlockedError} when {@link gateToolCallIds} would block. */
export async function assertToolCallsAllowed(
  toolCallIds: string[],
  ws: WebSocketClient | null,
  blockTimeoutSeconds?: number,
): Promise<void> {
  const result = await gateToolCallIds(toolCallIds, ws, blockTimeoutSeconds);
  if (result.action === "block") throw new AdrianPolicyBlockedError(result.reason);
}
