import { afterEach, describe, expect, it, vi } from "vitest";
import * as adrianCore from "@secureagentics/adrian";
import {
  BLOCKED_TOOL_MESSAGE,
  Mode,
  type EventData,
  type PairedEvent,
  type Verdict,
  type WebSocketClient,
} from "@secureagentics/adrian";
import { adrian } from "../src/index.js";

interface LangChainMessageLike {
  content: string;
  role?: string;
  type?: string;
  _getType?: () => string;
}

interface LangChainToolCallLike {
  id?: string;
  name?: string;
  args?: unknown;
  index?: number;
}

interface LangChainResultLike {
  content?: string;
  text?: string;
  tool_calls?: LangChainToolCallLike[];
  tool_call_chunks?: LangChainToolCallLike[];
  toolCalls?: LangChainToolCallLike[];
  usage_metadata?: Record<string, number>;
  response_metadata?: Record<string, unknown>;
}

interface LangChainRunnableLike {
  modelName?: string;
  model?: string;
  invoke(input: unknown, config?: unknown): Promise<LangChainResultLike>;
  stream?(input: unknown, config?: unknown): Promise<AsyncIterable<LangChainResultLike>>;
  bindTools?(tools: LangChainToolLike[]): LangChainRunnableLike;
}

interface LangChainToolLike {
  name?: string;
  invoke?(input: unknown, config?: unknown): Promise<unknown>;
  call?(input: unknown, config?: unknown): Promise<unknown>;
}

function human(content: string): LangChainMessageLike {
  return {
    content,
    _getType: () => "human",
  };
}

function ai(content: string): LangChainMessageLike {
  return {
    content,
    _getType: () => "ai",
  };
}

async function* langChainStream(chunks: LangChainResultLike[]) {
  for (const chunk of chunks) yield chunk;
}

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

describe("LangChain instrumentation", () => {
  afterEach(async () => {
    vi.restoreAllMocks();
    await adrian.shutdown();
  });

  it("captures runnable invoke calls as paired LLM events", async () => {
    const events: EventData[] = [];
    const model: LangChainRunnableLike = {
      modelName: "gpt-4o-mini",
      invoke: vi.fn(async () => ({
        content: "Use the documentation.",
        tool_calls: [{ id: "call-search", name: "search_docs", args: "{\"query\":\"langchain\"}" }],
        usage_metadata: { input_tokens: 8, output_tokens: 4, total_tokens: 12 },
      })),
    };
    const wrapped = adrian.langchain(model, { metadata: { tenantId: "tenant-1" } });
    const messages = [human("Where are the docs?"), ai("I will search.")];

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });

    const result = await wrapped.invoke(messages);

    expect(result.content).toBe("Use the documentation.");
    expect(model.invoke).toHaveBeenCalledWith(messages, undefined);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4o-mini",
      messages: [
        { role: "user", content: "Where are the docs?" },
        { role: "assistant", content: "I will search." },
      ],
      output: "Use the documentation.",
      toolCalls: [{ id: "call-search", name: "search_docs", args: { query: "langchain" } }],
      usage: { promptTokens: 8, completionTokens: 4, totalTokens: 12 },
    });
  });

  it("captures streamed chunks after the consumer drains the stream", async () => {
    const events: EventData[] = [];
    const model: LangChainRunnableLike = {
      model: "claude-3-5-sonnet",
      invoke: vi.fn(async () => ({ content: "" })),
      stream: vi.fn(async () => langChainStream([
        { content: "The lookup " },
        { tool_call_chunks: [{ index: 0, id: "call-lookup", name: "lookup_user", args: "{\"userId\"" }] },
        {
          content: "is ready.",
          tool_call_chunks: [{ index: 0, args: ":\"user_123\"}" }],
          usage_metadata: { input_tokens: 5, output_tokens: 6, total_tokens: 11 },
        },
      ])),
    };
    const wrapped = adrian.langchain(model);

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });

    const stream = await wrapped.stream!("Check user access.");
    const chunks: LangChainResultLike[] = [];
    for await (const chunk of stream) {
      chunks.push(chunk);
    }

    expect(chunks).toHaveLength(3);
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "claude-3-5-sonnet",
      messages: [{ role: "user", content: "Check user access." }],
      output: "The lookup is ready.",
      toolCalls: [{ id: "call-lookup", name: "lookup_user", args: { userId: "user_123" } }],
      usage: { promptTokens: 5, completionTokens: 6, totalTokens: 11 },
    });
  });

  it("wraps bindTools inputs and keeps the derived runnable instrumented", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    const lookupUser: LangChainToolLike = {
      name: "lookup_user",
      invoke: vi.fn(async () => ({ userId: "user_123", status: "active" })),
    };
    let boundTools: LangChainToolLike[] = [];
    const model: LangChainRunnableLike = {
      modelName: "gpt-4o-mini",
      invoke: vi.fn(async () => ({ content: "unbound" })),
      bindTools: vi.fn((tools) => {
        boundTools = tools;
        return {
          modelName: "gpt-4o-mini",
          invoke: vi.fn(async () => ({ content: "bound result" })),
        };
      }),
    };
    const wrapped = adrian.langchain(model);

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    const bound = wrapped.bindTools!([lookupUser]);
    const toolResult = await boundTools[0]!.invoke!({
      id: "call-lookup",
      name: "lookup_user",
      args: { userId: "user_123" },
    });
    const llmResult = await bound.invoke("Check user_123.");

    expect(model.bindTools).toHaveBeenCalledOnce();
    expect(boundTools[0]).not.toBe(lookupUser);
    expect(toolResult).toEqual({ userId: "user_123", status: "active" });
    expect(llmResult.content).toBe("bound result");
    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "lookup_user",
        toolCallId: "call-lookup",
        input: "{\"userId\":\"user_123\"}",
        output: "{\"userId\":\"user_123\",\"status\":\"active\"}",
      },
    });
    expect(events[1]).toMatchObject({
      type: "llm",
      data: {
        kind: "llm",
        model: "gpt-4o-mini",
        output: "bound result",
      },
    });
  });

  it("captures named tool maps and LangGraph tool call config", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    const toolMap: { lookup_user: LangChainToolLike } = {
      lookup_user: {
        call: vi.fn(async ({ userId }: { userId: string }) => `found ${userId}`),
      },
    };
    const tools = adrian.adrianTools(toolMap);

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    const result = await tools.lookup_user.call!(
      { userId: "user_123" },
      { state: { lg_tool_call: { id: "lg-call-1", name: "lookup_user", args: { userId: "user_123" } } } },
    );

    expect(result).toBe("found user_123");
    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "lookup_user",
        toolCallId: "lg-call-1",
        input: "{\"userId\":\"user_123\"}",
        output: "found user_123",
      },
    });
  });

  it("blocks LangChain tool execution when policy halts the tool call", async () => {
    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, blockTimeout: 5 });
    vi.spyOn(adrianCore, "getWebSocketClient").mockReturnValue(mockWs(true));

    let executed = false;
    const rawTools: LangChainToolLike[] = [{
      name: "lookup_user",
      invoke: vi.fn(async () => {
        executed = true;
        return { ok: true };
      }),
    }];
    const tools = adrian.adrianTools(rawTools);

    const result = await tools[0]!.invoke!({
      id: "call-lookup",
      name: "lookup_user",
      args: { userId: "user_123" },
    });

    expect(result).toBe(BLOCKED_TOOL_MESSAGE);
    expect(executed).toBe(false);
  });

  it("reuses the active invocation for runnable and tool events", async () => {
    const pairedEvents: PairedEvent[] = [];
    const model = adrian.langchain<LangChainRunnableLike>({
      modelName: "gpt-4o-mini",
      invoke: vi.fn(async () => ({
        content: "",
        tool_calls: [{ id: "call-lookup", name: "lookup_user", args: { userId: "user_123" } }],
      })),
    });
    const rawTools: LangChainToolLike[] = [{
      name: "lookup_user",
      invoke: vi.fn(async () => ({ userId: "user_123", status: "active" })),
    }];
    const tools = adrian.adrianTools(rawTools);

    await adrian.init({
      handlers: [{
        onPairedEvent(event) {
          pairedEvents.push(event);
        },
        close() {},
      }],
      sessionId: "sess",
      wsUrl: null,
    });

    await adrianCore.runWithInvocationId("inv-langchain", async () => {
      const response = await model.invoke("Check user_123.");
      await tools[0]!.invoke!(response.tool_calls![0]);
    });

    expect(pairedEvents).toHaveLength(2);
    expect(pairedEvents.map((event) => event.invocationId)).toEqual(["inv-langchain", "inv-langchain"]);
    expect(pairedEvents.map((event) => event.pairType)).toEqual(["llm", "tool"]);
  });
});
