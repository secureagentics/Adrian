import { afterEach, describe, expect, it, vi } from "vitest";
import * as adrianCore from "@secureagentics/adrian";
import { BLOCKED_TOOL_MESSAGE, Mode, type EventData, type Verdict, type WebSocketClient } from "@secureagentics/adrian";
import { adrian } from "../src/index.js";

function mockOpenAIStream<T>(chunks: T[]) {
  const controller = new AbortController();
  async function* sourceIterator() {
    for (const chunk of chunks) yield chunk;
  }

  const stream = {
    controller,
    async *[Symbol.asyncIterator]() {
      yield* sourceIterator();
    },
    tee() {
      const left: Array<Promise<IteratorResult<T>>> = [];
      const right: Array<Promise<IteratorResult<T>>> = [];
      const iterator = sourceIterator();
      const branch = (queue: Array<Promise<IteratorResult<T>>>) => ({
        next: () => {
          if (queue.length === 0) {
            const result = iterator.next();
            left.push(result);
            right.push(result);
          }
          return queue.shift()!;
        },
      });
      const branchStream = (iter: () => AsyncIterator<T>) => ({
        controller,
        [Symbol.asyncIterator]: iter,
        tee: stream.tee,
        toReadableStream: stream.toReadableStream,
      });
      return [branchStream(() => branch(left)), branchStream(() => branch(right))] as [typeof stream, typeof stream];
    },
    toReadableStream() {
      let iter: AsyncIterator<T> | undefined;
      return new ReadableStream<Uint8Array>({
        pull: async (ctrl) => {
          iter ??= (this as AsyncIterable<T>)[Symbol.asyncIterator]();
          const { value, done } = await iter.next();
          if (done) ctrl.close();
          else ctrl.enqueue(new TextEncoder().encode(`${JSON.stringify(value)}\n`));
        },
      });
    },
  };

  return stream;
}

interface StreamLike<T> extends AsyncIterable<T> {
  controller: AbortController;
  tee(): [StreamLike<T>, StreamLike<T>];
  toReadableStream(): ReadableStream<Uint8Array>;
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

describe("OpenAI instrumentation", () => {
  afterEach(async () => {
    vi.restoreAllMocks();
    await adrian.shutdown();
  });

  it("captures chat completion calls as paired LLM events", async () => {
    const events: EventData[] = [];
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => ({
            choices: [{
              message: {
                content: "hello",
                tool_calls: [{
                  id: "call-1",
                  function: { name: "search", arguments: "{\"query\":\"docs\"}" },
                }],
              },
            }],
            usage: { prompt_tokens: 3, completion_tokens: 4, total_tokens: 7 },
          }),
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });
    const result = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "system", content: "be brief" }, { role: "user", content: "hi" }],
    });

    expect(result.choices[0]?.message.content).toBe("hello");
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4o-mini",
      output: "hello",
      usage: { promptTokens: 3, completionTokens: 4, totalTokens: 7 },
    });
    expect("toolCalls" in events[0] && events[0].toolCalls[0]).toMatchObject({ id: "call-1", name: "search", args: { query: "docs" } });
  });

  it("captures responses API calls", async () => {
    const events: EventData[] = [];
    const client = adrian.openai({
      responses: {
          create: async (_body: Record<string, unknown>) => ({
          output_text: "done",
          output: [{ type: "function_call", call_id: "call-2", name: "lookup", arguments: "{\"id\":42}" }],
          usage: { input_tokens: 5, output_tokens: 6, total_tokens: 11 },
        }),
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });
    await client.responses.create({ model: "gpt-4.1", input: "run lookup" });

    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4.1",
      output: "done",
      toolCalls: [{ id: "call-2", name: "lookup", args: { id: 42 } }],
    });
  });

  it("captures responses API streaming tool calls and text", async () => {
    const events: EventData[] = [];
    async function* stream() {
      yield { type: "response.output_text.delta", delta: "The answer " };
      yield { type: "response.output_item.added", item: { id: "item-1", type: "function_call", call_id: "call-4", name: "lookup" } };
      yield { type: "response.function_call_arguments.delta", item_id: "item-1", delta: "{\"id\"" };
      yield { type: "response.function_call_arguments.delta", item_id: "item-1", delta: ":7}" };
      yield { type: "response.output_text.delta", delta: "is ready." };
    }
    const client = adrian.openai({
      responses: {
        create: async (_body: Record<string, unknown>) => stream(),
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });
    const result = await client.responses.create({ model: "gpt-4.1", input: "lookup id 7", stream: true });
    for await (const _chunk of result) {
      // consume the stream so Adrian can emit the paired event
    }

    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4.1",
      output: "The answer is ready.",
      toolCalls: [{ id: "call-4", name: "lookup", args: { id: 7 } }],
    });
  });

  it("captures Responses API instructions and array input for stream and non-stream", async () => {
    const responseBody = {
      instructions: "You are an autonomous assistant.",
      input: [
        { role: "user", content: "Do the work." },
        { type: "function_call", call_id: "call-1", name: "get_weather", arguments: '{"city":"SF"}' },
        { type: "function_call_output", call_id: "call-1", output: '{"temp":58}' },
      ],
    };

    for (const stream of [false, true]) {
      const events: EventData[] = [];
      async function* responseStream() {
        yield { type: "response.output_text.delta", delta: "Done." };
      }
      const client = adrian.openai({
        responses: {
          create: async (_body: Record<string, unknown>) => (
            stream ? responseStream() : { output_text: "Done.", usage: { input_tokens: 1, output_tokens: 1, total_tokens: 2 } }
          ),
        },
      });

      await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
        events.push(data);
      } });

      const result = await client.responses.create({
        model: "gpt-4o-mini",
        ...responseBody,
        stream,
      });

      if (stream) {
        for await (const _chunk of result as AsyncIterable<unknown>) {
          // consume stream
        }
      }

      expect(events[0]).toMatchObject({
        kind: "llm",
        model: "gpt-4o-mini",
        messages: [
          { role: "system", content: "You are an autonomous assistant." },
          { role: "user", content: "Do the work." },
          { role: "assistant", content: '[tool_call:get_weather] {"city":"SF"}' },
          { role: "tool", content: '{"temp":58}' },
        ],
      });

      await adrian.shutdown();
    }
  });

  it("preserves OpenAI stream helper methods when Adrian is enabled", async () => {
    const events: EventData[] = [];
    const source = mockOpenAIStream([
      { choices: [{ delta: { content: "hello" } }] },
    ]);
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => source,
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });

    const result = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "stream" }],
      stream: true,
    }) as StreamLike<unknown>;

    expect(result.controller).toBe(source.controller);
    expect(typeof result.tee).toBe("function");
    expect(typeof result.toReadableStream).toBe("function");

    const reader = result.toReadableStream().getReader();
    const { value } = await reader.read();
    expect(new TextDecoder().decode(value)).toBe('{"choices":[{"delta":{"content":"hello"}}]}\n');
    while (true) {
      const next = await reader.read();
      if (next.done) break;
    }

    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4o-mini",
      output: "hello",
    });
  });

  it("preserves tee() while capturing a single paired LLM event", async () => {
    const events: EventData[] = [];
    const source = mockOpenAIStream([
      { choices: [{ delta: { content: "hello" } }] },
      { choices: [{ delta: { content: " world" } }] },
    ]);
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => source,
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });

    const result = await client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "stream" }],
      stream: true,
    }) as StreamLike<unknown>;

    const [left, right] = result.tee();
    expect(left.controller).toBe(source.controller);
    expect(typeof left.toReadableStream).toBe("function");

    for await (const _chunk of left) {
      // consume one tee branch
    }
    for await (const _chunk of right) {
      // consume the other branch
    }

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4o-mini",
      output: "hello world",
    });
  });

  it("emits partial stream data when the consumer stops early", async () => {
    const events: EventData[] = [];
    async function* stream() {
      yield { choices: [{ delta: { content: "first " } }] };
      yield { choices: [{ delta: { content: "second" } }] };
    }
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => stream(),
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });
    const result = await client.chat.completions.create({ model: "gpt-4o-mini", messages: [{ role: "user", content: "stream" }], stream: true });
    for await (const _chunk of result) {
      break;
    }

    expect(events[0]).toMatchObject({
      kind: "llm",
      model: "gpt-4o-mini",
      output: "first ",
    });
  });

  it("blocks captureTool when policy halts", async () => {
    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, blockTimeout: 5 });
    vi.spyOn(adrianCore, "getWebSocketClient").mockReturnValue(mockWs(true));

    let executed = false;
    const result = await adrian.captureTool({
      id: "call-weather",
      function: { name: "get_weather", arguments: "{}" },
    }, async () => {
      executed = true;
      return { ok: true };
    });

    expect(result).toBe(BLOCKED_TOOL_MESSAGE);
    expect(executed).toBe(false);
  });

  it("captures local OpenAI tool execution as a tool event", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    const result = await adrian.captureTool({
      id: "call-weather",
      type: "function",
      function: { name: "get_weather", arguments: "{\"city\":\"San Francisco\"}" },
    }, async () => ({ temperatureF: 58, condition: "cloudy" }));

    expect(result).toEqual({ temperatureF: 58, condition: "cloudy" });
    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "get_weather",
        toolCallId: "call-weather",
        input: "{\"city\":\"San Francisco\"}",
        output: "{\"temperatureF\":58,\"condition\":\"cloudy\"}",
      },
    });
  });

  it("captures local OpenAI tool execution errors as tool events", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    await expect(adrian.captureTool({
      id: "call-weather",
      type: "function",
      function: { name: "get_weather", arguments: "{\"city\":\"San Francisco\"}" },
    }, async () => {
      throw new Error("weather API unavailable");
    })).rejects.toThrow("weather API unavailable");

    expect(events[0]).toMatchObject({
      type: "tool",
      data: {
        kind: "tool",
        toolName: "get_weather",
        toolCallId: "call-weather",
        output: "[ERROR] Error: weather API unavailable",
        error: { name: "Error", message: "weather API unavailable" },
      },
    });
  });

  it("captures OpenAI request errors as LLM events", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => {
            throw new Error("rate limited");
          },
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    await expect(client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
    })).rejects.toThrow("rate limited");

    expect(events[0]).toMatchObject({
      type: "llm",
      data: {
        kind: "llm",
        model: "gpt-4o-mini",
        output: "[ERROR] Error: rate limited",
        error: { name: "Error", message: "rate limited" },
      },
    });
  });

  it("captures streaming OpenAI request errors as LLM events", async () => {
    const events: Array<{ type: string; data: EventData }> = [];
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => {
            throw new Error("rate limited");
          },
        },
      },
      responses: {
        create: async (_body: Record<string, unknown>) => {
          throw new Error("responses rate limited");
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (type, data) => {
      events.push({ type, data });
    } });

    await expect(client.chat.completions.create({
      model: "gpt-4o-mini",
      messages: [{ role: "user", content: "hi" }],
      stream: true,
    })).rejects.toThrow("rate limited");

    await expect(client.responses.create({
      model: "gpt-4.1",
      input: "hi",
      stream: true,
    })).rejects.toThrow("responses rate limited");

    expect(events).toHaveLength(2);
    expect(events[0]).toMatchObject({
      type: "llm",
      data: {
        kind: "llm",
        model: "gpt-4o-mini",
        output: "[ERROR] Error: rate limited",
        error: { name: "Error", message: "rate limited" },
      },
    });
    expect(events[1]).toMatchObject({
      type: "llm",
      data: {
        kind: "llm",
        model: "gpt-4.1",
        output: "[ERROR] Error: responses rate limited",
        error: { name: "Error", message: "responses rate limited" },
      },
    });
  });

  it("wraps OpenAI client via adrian.openai()", async () => {
    const events: EventData[] = [];
    const client = adrian.openai({
      chat: {
        completions: {
          create: async (_body: Record<string, unknown>) => ({
            choices: [{ message: { content: "hello" } }],
          }),
        },
      },
    });

    await adrian.init({ handlers: [], sessionId: "sess", wsUrl: null, onEvent: (_type, data) => {
      events.push(data);
    } });

    await client.chat.completions.create({
      model: "gpt-4o",
      messages: [{ role: "user", content: "hi" }],
    });

    expect(events).toHaveLength(1);
    expect(events[0]).toMatchObject({ model: "gpt-4o", output: "hello" });
  });
});
