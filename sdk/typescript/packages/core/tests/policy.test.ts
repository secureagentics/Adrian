import { afterEach, describe, expect, it } from "vitest";
import { setConfig, Mode, type Verdict, type WebSocketClient } from "../src/index.js";
import { assertToolCallsAllowed, gateToolCallIds } from "../src/policy.js";

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

describe("gateToolCallIds", () => {
  afterEach(() => setConfig(null));

  it("allows when WebSocket is absent", async () => {
    expect(await gateToolCallIds(["call-1"], null)).toEqual({ action: "allow" });
  });

  it("allows when policy is not active", async () => {
    setConfig(config());
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => false,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async () => verdict("evt", false),
    } as unknown as WebSocketClient;
    expect(await gateToolCallIds(["call-1"], ws)).toEqual({ action: "allow" });
  });

  it("blocks on missing tool call id", async () => {
    setConfig(config());
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async () => verdict("evt", false),
    } as unknown as WebSocketClient;
    expect(await gateToolCallIds(["call-1", ""], ws)).toEqual({ action: "block", reason: "missing_tool_call_id" });
  });

  it("blocks when a verdict requests halt", async () => {
    setConfig(config());
    const waitedFor: string[] = [];
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async (toolCallId: string) => {
        waitedFor.push(toolCallId);
        return verdict(`event-${toolCallId}`, toolCallId === "call-2");
      },
    } as unknown as WebSocketClient;
    expect(await gateToolCallIds(["call-1", "call-2"], ws)).toEqual({ action: "block", reason: "policy_halt" });
    expect(waitedFor).toEqual(["call-1", "call-2"]);
  });

  it("allows when all verdicts permit execution", async () => {
    setConfig(config());
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async (toolCallId: string) => verdict(`event-${toolCallId}`, false),
    } as unknown as WebSocketClient;
    expect(await gateToolCallIds(["call-1", "call-2"], ws)).toEqual({ action: "allow" });
  });

  it("allows when verdicts time out (fail-open)", async () => {
    setConfig(config());
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async () => null,
    } as unknown as WebSocketClient;
    expect(await gateToolCallIds(["call-1"], ws)).toEqual({ action: "allow" });
  });

  it("assertToolCallsAllowed allows on verdict timeout (fail-open)", async () => {
    setConfig(config());
    const ws = {
      waitForPolicyReady: async () => true,
      policyActive: () => true,
      blockTimeout: (seconds: number) => seconds,
      waitForToolCallVerdict: async () => null,
    } as unknown as WebSocketClient;
    await expect(assertToolCallsAllowed(["call-1"], ws)).resolves.toBeUndefined();
  });
});
