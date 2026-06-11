import { describe, expect, it } from "vitest";
import type { PairedEvent } from "../src/format/types.js";
import { Mode, type Verdict } from "../src/proto/schema.js";
import { WebSocketClient } from "../src/ws.js";

function client(): WebSocketClient {
  return new WebSocketClient({ url: "ws://localhost:0", sessionId: "sess", apiKey: "key", replayBufferFrames: 10 });
}

function verdict(eventId: string): Verdict {
  return {
    eventId,
    sessionId: "sess",
    madCode: "M3_TEST",
    policy: { mode: Mode.MODE_BLOCK, policyM0: false, policyM2: false, policyM3: true, policyM4: false },
    hitl: null,
  };
}

function llmEvent(eventId: string): PairedEvent {
  return {
    eventId,
    invocationId: "inv",
    sessionId: "sess",
    runId: "run",
    parentRunId: "",
    timestamp: new Date(0).toISOString(),
    pairType: "llm",
    agent: { agentId: "agent", systemPrompt: "", userInstruction: "" },
    parent: null,
    data: { kind: "llm", model: "ChatOpenAI", messages: [], output: "", toolCalls: [{ id: "tool-1", name: "search", args: {} }], usage: null },
    metadata: null,
  };
}

describe("WebSocketClient verdict waiting", () => {
  it("replays a verdict that arrives before a waiter is registered", async () => {
    const ws = client();
    const early = verdict("evt-1");
    (ws as unknown as { resolveVerdict: (eventId: string, verdict: Verdict) => void }).resolveVerdict("evt-1", early);

    await expect(ws.waitForVerdict("evt-1", 1)).resolves.toBe(early);
  });

  it("resolves every waiter registered for the same event", async () => {
    const ws = client();
    const expected = verdict("evt-2");
    const first = ws.waitForVerdict("evt-2", 1);
    const second = ws.waitForVerdict("evt-2", 1);

    (ws as unknown as { resolveVerdict: (eventId: string, verdict: Verdict) => void }).resolveVerdict("evt-2", expected);

    await expect(Promise.all([first, second])).resolves.toEqual([expected, expected]);
  });

  it("uses cached event verdicts for correlated tool calls", async () => {
    const ws = client();
    await ws.onPairedEvent(llmEvent("evt-3"));
    const expected = verdict("evt-3");
    (ws as unknown as { resolveVerdict: (eventId: string, verdict: Verdict) => void }).resolveVerdict("evt-3", expected);

    await expect(ws.waitForToolCallVerdict("tool-1", 1)).resolves.toBe(expected);
  });
});
