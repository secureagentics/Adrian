import { afterEach, describe, expect, it, vi } from "vitest";
import * as adrianCore from "@secureagentics/adrian";
import { AdrianPolicyBlockedError, init, Mode, shutdown, type EventData, type Verdict, type WebSocketClient } from "@secureagentics/adrian";
import { captureTool, adrian, adrianTools } from "../src/index.js";

function mockWs(halt: boolean): WebSocketClient {
  return {
    waitForPolicyReady: async () => true,
    policyActive: () => true,
    blockTimeout: (seconds: number) => seconds,
    waitForToolCallVerdict: async (toolCallId: string) => ({
      eventId: `event-${toolCallId}`,
      sessionId: "sess",
      madCode: "M3_TEST",
      policy: { mode: Mode.MODE_BLOCK, policyM0: false, policyM2: false, policyM3: halt, policyM4: false },
      hitl: null,
    } satisfies Verdict),
  } as unknown as WebSocketClient;
}

describe("Vercel AI SDK instrumentation", () => {
  afterEach(async () => {
    vi.restoreAllMocks();
    await shutdown();
  });

  it("captures generateText calls as paired LLM events", async () => {
    const events: EventData[] = [];
    const ai = adrian({
      generateText: async (_args: Record<string, unknown>) => ({
        text: "hello from vercel",
        toolCalls: [{ toolCallId: "tool-1", toolName: "search", args: { query: "adrian" } }],
        usage: { promptTokens: 8, completionTokens: 9, totalTokens: 17 },
      }),
    });

    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });
    const result = await ai.generateText({
      model: { modelId: "openai/gpt-4o-mini" },
      system: "be brief",
      prompt: "hello",
    });

    expect(result.text).toBe("hello from vercel");
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "openai/gpt-4o-mini",
      output: "hello from vercel",
      toolCalls: [{ id: "tool-1", name: "search", args: { query: "adrian" } }],
      usage: { promptTokens: 8, completionTokens: 9, totalTokens: 17 },
    });
  });

  it("emits streamText events when the result promises settle", async () => {
    const events: EventData[] = [];
    const ai = adrian({
      streamText: (_args: Record<string, unknown>) => ({
        text: Promise.resolve("streamed"),
        toolCalls: Promise.resolve([]),
        usage: Promise.resolve({ inputTokens: 2, outputTokens: 3, totalTokens: 5 }),
      }),
    });

    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });
    const result = await ai.streamText({ model: "gpt-4o", messages: [{ role: "user", content: "hi" }] });
    await result.text;

    await new Promise((resolve) => setTimeout(resolve, 0));
    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4o",
      output: "streamed",
      usage: { promptTokens: 2, completionTokens: 3, totalTokens: 5 },
    });
  });

  it("blocks captureTool when policy halts", async () => {
    await init({ handlers: [], sessionId: "sess", wsUrl: null, blockTimeout: 5 });
    vi.spyOn(adrianCore, "getWebSocketClient").mockReturnValue(mockWs(true));

    let executed = false;
    await expect(captureTool({
      toolCallId: "tool-weather",
      toolName: "getWeather",
      args: { city: "SF" },
    }, async () => {
      executed = true;
      return { ok: true };
    })).rejects.toBeInstanceOf(AdrianPolicyBlockedError);

    expect(executed).toBe(false);
  });

  it("blocks adrianTools execute when policy halts", async () => {
    await init({ handlers: [], sessionId: "sess", wsUrl: null, blockTimeout: 5 });
    vi.spyOn(adrianCore, "getWebSocketClient").mockReturnValue(mockWs(true));

    const tools = adrianTools({
      getWeather: {
        description: "Get weather",
        execute: async () => ({ temp: 72 }),
      },
    });

    await expect(tools.getWeather.execute({ city: "SF" }, { toolCallId: "tool-weather" })).rejects.toBeInstanceOf(AdrianPolicyBlockedError);
  });

  it("captures local Vercel AI tool execution as a tool event", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    const result = await captureTool({
      toolCallId: "tool-weather",
      toolName: "getWeather",
      args: { city: "San Francisco" },
    }, async () => ({ temperatureF: 58, condition: "cloudy" }));

    expect(result).toEqual({ temperatureF: 58, condition: "cloudy" });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "getWeather",
        toolCallId: "tool-weather",
        input: "{\"city\":\"San Francisco\"}",
        output: "{\"temperatureF\":58,\"condition\":\"cloudy\"}",
      },
    });
  });

  it("captures local Vercel AI tool errors as tool events", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    await expect(captureTool({
      toolCallId: "tool-weather",
      toolName: "getWeather",
      args: { city: "San Francisco" },
    }, async () => {
      throw new Error("weather API unavailable");
    })).rejects.toThrow("weather API unavailable");

    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "getWeather",
        toolCallId: "tool-weather",
        output: "[ERROR] Error: weather API unavailable",
        error: { name: "Error", message: "weather API unavailable" },
      },
    });
  });

  it("wraps Vercel AI SDK tool execute functions", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    const tools = adrianTools({
      getWeather: {
        description: "Get current weather for a city.",
        execute: async ({ city }: { city: string }, _options?: unknown) => ({ city, temperatureF: 58 }),
      },
    });

    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    const result = await tools.getWeather.execute({ city: "San Francisco" }, { toolCallId: "tool-weather" });

    expect(result).toEqual({ city: "San Francisco", temperatureF: 58 });
    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "getWeather",
        toolCallId: "tool-weather",
        input: "{\"city\":\"San Francisco\"}",
        output: "{\"city\":\"San Francisco\",\"temperatureF\":58}",
      },
    });
  });

  it("wraps Vercel AI SDK via adrian()", async () => {
    const events: EventData[] = [];
    const ai = adrian({
      generateText: async () => ({
        text: "hello from vercel",
      }),
    });

    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });

    await ai.generateText({
      model: "gpt-4o",
      prompt: "hello",
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ model: "gpt-4o", output: "hello from vercel" });
  });

  it("wraps Vercel tools via adrian()", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    const tools = adrian({
      getWeather: {
        description: "Get weather",
        execute: async ({ city }: { city: string }) => ({ city, temp: 72 }),
      },
    });

    await init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    await tools.getWeather.execute({ city: "SF" });

    expect(events).toHaveLength(1);
    expect(events[0].type).toBe("tool");
    expect(events[0].data).toMatchObject({ toolName: "getWeather", input: '{"city":"SF"}' });
  });
});
