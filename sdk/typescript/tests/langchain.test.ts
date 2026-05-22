import { afterEach, describe, expect, it } from "vitest";
import { setConfig } from "../src/config.js";
import { blockedToolNodeResponse } from "../src/instrumentation/langchain.js";
import { Mode, type Verdict } from "../src/proto/schema.js";
import type { WebSocketClient } from "../src/ws.js";

function config(): Parameters<typeof setConfig>[0] {
  return {
    apiKey: null,
    logFile: "events.jsonl",
    logLevel: null,
    sessionId: "sess",
    wsUrl: null,
    blockTimeout: 5,
    onEvent: null,
    onVerdict: null,
    onBlock: null,
    onAudit: null,
    onDisconnect: null,
    onReconnect: null,
    onMcpServer: null,
    replayBufferFrames: 1000,
  };
}

function verdict(eventId: string, halt: boolean): Verdict {
  return {
    eventId,
    sessionId: "sess",
    madCode: "M3_TEST",
    policy: { mode: Mode.MODE_BLOCK, policyM0: false, policyM2: false, policyM3: halt, policyM4: false },
    hitl: null,
  };
}

describe("ToolNode policy gating", () => {
  afterEach(() => setConfig(null));

  it("waits for policy readiness and evaluates every tool call verdict", async () => {
    setConfig(config());
    const waitedFor: string[] = [];
    let waitedForPolicy = false;
    const ws = {
      waitForPolicyReady: async () => {
        waitedForPolicy = true;
        return true;
      },
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async (toolCallId: string) => {
        waitedFor.push(toolCallId);
        return verdict(`event-${toolCallId}`, toolCallId === "call-2");
      },
    } as unknown as WebSocketClient;

    const response = await blockedToolNodeResponse({
      messages: [{ tool_calls: [{ id: "call-1", name: "search" }, { id: "call-2", name: "write" }] }],
    }, ws);

    expect(waitedForPolicy).toBe(true);
    expect(waitedFor).toEqual(["call-1", "call-2"]);
    expect(response?.messages).toHaveLength(2);
    expect(response?.messages[0]?.content).toContain("BLOCKED");
  });

  it("does not block when all correlated verdicts allow execution", async () => {
    setConfig(config());
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async (toolCallId: string) => verdict(`event-${toolCallId}`, false),
    } as unknown as WebSocketClient;

    await expect(blockedToolNodeResponse({
      messages: [{ toolCalls: [{ id: "call-1", name: "search" }, { id: "call-2", name: "write" }] }],
    }, ws)).resolves.toBeNull();
  });
});
