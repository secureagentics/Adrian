import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";
import type { PairedEvent } from "../src/format/types.js";
import { Mode, type Verdict } from "../src/proto/schema.js";

const wsMock = vi.hoisted(() => {
  const { EventEmitter } = require("node:events") as typeof import("node:events");
  const created: InstanceType<typeof EventEmitter>[] = [];
  let closeCodeOnOpen = 4003;

  class MockWebSocket extends EventEmitter {
    static readonly CONNECTING = 0;
    static readonly OPEN = 1;
    static readonly CLOSING = 2;
    static readonly CLOSED = 3;
    readyState = MockWebSocket.CONNECTING;
    binaryType = "arraybuffer";

    constructor(
      public url: string,
      public options?: unknown,
    ) {
      super();
      created.push(this);
      queueMicrotask(() => {
        if (this.readyState === MockWebSocket.CLOSED) return;
        this.readyState = MockWebSocket.OPEN;
        this.emit("open");
        queueMicrotask(() => {
          this.readyState = MockWebSocket.CLOSED;
          this.emit("close", closeCodeOnOpen);
        });
      });
    }

    send(_data: unknown, _opts: unknown, cb?: (err?: Error | null) => void): void {
      cb?.(null);
    }

    close(code?: number): void {
      if (this.readyState === MockWebSocket.CLOSED) return;
      this.readyState = MockWebSocket.CLOSED;
      this.emit("close", code ?? 1000);
    }
  }

  return {
    created,
    get closeCodeOnOpen() {
      return closeCodeOnOpen;
    },
    set closeCodeOnOpen(value: number) {
      closeCodeOnOpen = value;
    },
    reset(): void {
      created.length = 0;
      closeCodeOnOpen = 4003;
    },
    MockWebSocket,
  };
});

vi.mock("ws", () => ({
  default: wsMock.MockWebSocket,
}));

import { WebSocketClient } from "../src/ws.js";

const QUOTA_RECONNECT_DELAY_MS = 60_000;

function client(onDisconnect?: (reason: string) => void): WebSocketClient {
  return new WebSocketClient({
    url: "ws://localhost:0",
    sessionId: "sess",
    apiKey: "key",
    replayBufferFrames: 10,
    onDisconnect,
  });
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

function nextReconnectDelay(ws: WebSocketClient): number | null {
  return (ws as unknown as { nextReconnectDelay: number | null }).nextReconnectDelay;
}

async function flushConnectionLifecycle(): Promise<void> {
  await vi.advanceTimersByTimeAsync(0);
  await vi.advanceTimersByTimeAsync(0);
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

describe("WebSocketClient quota-exhausted reconnect", () => {
  beforeEach(() => {
    vi.useFakeTimers();
    wsMock.reset();
  });

  afterEach(() => {
    vi.useRealTimers();
    wsMock.reset();
  });

  it("arms a 60s reconnect delay when the server closes with 4003", async () => {
    const disconnects: string[] = [];
    const ws = client((reason) => disconnects.push(reason));
    ws.scheduleConnect();

    await flushConnectionLifecycle();

    expect(disconnects).toEqual(["quota_exhausted (close=4003)"]);
    expect(wsMock.created).toHaveLength(1);
    expect(nextReconnectDelay(ws)).toBeNull();

    await vi.advanceTimersByTimeAsync(QUOTA_RECONNECT_DELAY_MS - 1);
    expect(wsMock.created).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1);
    expect(wsMock.created).toHaveLength(2);

    await ws.close();
  });

  it("leaves reconnect delay unset after a normal close", async () => {
    wsMock.closeCodeOnOpen = 1000;
    const disconnects: string[] = [];
    const ws = client((reason) => disconnects.push(reason));
    ws.scheduleConnect();

    await flushConnectionLifecycle();

    expect(disconnects).toEqual(["recv_loop_exit"]);
    expect(nextReconnectDelay(ws)).toBeNull();

    await vi.advanceTimersByTimeAsync(999);
    expect(wsMock.created).toHaveLength(1);

    await vi.advanceTimersByTimeAsync(1);
    expect(wsMock.created).toHaveLength(2);

    await ws.close();
  });

  it("consumes a pending reconnect delay once before the next connect attempt", async () => {
    const ws = client();
    (ws as unknown as { nextReconnectDelay: number | null }).nextReconnectDelay = QUOTA_RECONNECT_DELAY_MS;
    ws.scheduleConnect();

    expect(wsMock.created).toHaveLength(0);
    expect(nextReconnectDelay(ws)).toBeNull();

    await vi.advanceTimersByTimeAsync(QUOTA_RECONNECT_DELAY_MS - 1);
    expect(wsMock.created).toHaveLength(0);

    await vi.advanceTimersByTimeAsync(1);
    expect(wsMock.created).toHaveLength(1);

    await ws.close();
  });
});
