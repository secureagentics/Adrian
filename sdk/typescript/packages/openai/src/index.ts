import { randomUUID } from "node:crypto";
import { assertToolCallsAllowed, currentConfig, getHandler, getWebSocketClient, runWithInvocationId } from "@secureagentics/adrian";
import type { CallbackMetadata, LlmEndData, ToolCallRecord } from "@secureagentics/adrian";
import {
  captureLlmAsyncIterable,
  captureLlmCall,
  gateLlmEndData,
  emptyLlmEnd,
  messagesFromPromptLike,
  normalizeMessages,
  normalizeUsage,
  parseToolArgs,
  stringifyContent,
} from "@secureagentics/adrian/capture";

export interface AdrianOptions {
  metadata?: CallbackMetadata | null;
}

export interface ToolCallLike {
  id: string;
  type?: string;
  function?: {
    name?: string;
    arguments?: string;
  };
  name?: string;
  arguments?: string;
}

export interface ToolCaptureOptions {
  metadata?: CallbackMetadata | null;
  parentRunId?: string;
}

/** Wrap an OpenAI client so Adrian captures LLM and tool events. */
export function adrian<T extends object>(client: T, options: AdrianOptions = {}): T {
  return new Proxy(client, {
    get(target, prop, receiver) {
      if (prop === "chat") return instrumentChat(Reflect.get(target, prop, receiver), options);
      if (prop === "responses") return instrumentResponses(Reflect.get(target, prop, receiver), options);
      return Reflect.get(target, prop, receiver);
    },
  });
}

/** Wrap manual OpenAI tool execution so Adrian captures tool events. */
export async function captureTool<T>(
  toolCall: ToolCallLike,
  execute: () => T | Promise<T>,
  options: ToolCaptureOptions = {},
): Promise<T> {
  const handler = getHandler();
  if (!handler) return execute();

  const runId = randomUUID();
  const toolName = String(toolCall.function?.name ?? toolCall.name ?? "unknown");
  const toolCallId = String(toolCall.id ?? "");
  const input = String(toolCall.function?.arguments ?? toolCall.arguments ?? "");
  const metadata = integrationMetadata(options.metadata, "openai.tool_call");

  await assertToolCallsAllowed(toolCallId ? [toolCallId] : [], getWebSocketClient(), currentConfig()?.blockTimeout ?? 30);

  return runWithInvocationId(randomUUID(), async () => {
    await handler.handleToolStart({ name: toolName }, input, runId, options.parentRunId, { metadata, tool_call_id: toolCallId });
    try {
      const result = await execute();
      await handler.handleToolEnd(result, runId);
      return result;
    } catch (error) {
      await handler.handleToolError(error, runId);
      throw error;
    }
  });
}

function instrumentChat(chat: unknown, options: AdrianOptions): unknown {
  if (!chat || typeof chat !== "object") return chat;
  return new Proxy(chat as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      if (prop === "completions") return instrumentChatCompletions(Reflect.get(target, prop, receiver), options);
      return Reflect.get(target, prop, receiver);
    },
  });
}

function instrumentChatCompletions(completions: unknown, options: AdrianOptions): unknown {
  if (!completions || typeof completions !== "object") return completions;
  return new Proxy(completions as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (prop !== "create" || typeof value !== "function") return value;
      return async function adrianOpenAIChatCreate(this: unknown, body: Record<string, unknown> = {}, ...rest: unknown[]) {
        const model = String(body.model ?? "openai");
        const metadata = integrationMetadata(options.metadata, "openai.chat.completions");
        const messages = normalizeMessages(body.messages);
        if (body.stream === true) {
          const result = await Promise.resolve(value.call(target, body, ...rest));
          if (isAsyncIterable(result)) return captureChatCompletionStream(model, messages, metadata, result);
          return result;
        }
        return captureLlmCall(getHandler, { model, messages, metadata }, () => Promise.resolve(value.call(target, body, ...rest)), extractChatCompletion, gateLlmEndData);
      };
    },
  });
}

function instrumentResponses(responses: unknown, options: AdrianOptions): unknown {
  if (!responses || typeof responses !== "object") return responses;
  return new Proxy(responses as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (prop !== "create" || typeof value !== "function") return value;
      return async function adrianOpenAIResponsesCreate(this: unknown, body: Record<string, unknown> = {}, ...rest: unknown[]) {
        const model = String(body.model ?? "openai");
        const metadata = integrationMetadata(options.metadata, "openai.responses");
        const messages = messagesFromPromptLike({ input: body.input });
        if (body.stream === true) {
          const result = await Promise.resolve(value.call(target, body, ...rest));
          if (isAsyncIterable(result)) return captureResponseStream(model, messages, metadata, result);
          return result;
        }
        return captureLlmCall(getHandler, { model, messages, metadata }, () => Promise.resolve(value.call(target, body, ...rest)), extractResponse, gateLlmEndData);
      };
    },
  });
}

function captureChatCompletionStream(model: string, messages: ReturnType<typeof normalizeMessages>, metadata: CallbackMetadata | null, stream: AsyncIterable<unknown>): AsyncIterable<unknown> {
  let output = "";
  let usage: LlmEndData["usage"] = null;
  const toolCallParts = new Map<number, { id: string; name: string; args: string }>();
  return captureLlmAsyncIterable(getHandler, { model, messages, metadata }, stream, (chunk) => {
    const obj = chunk && typeof chunk === "object" ? chunk as Record<string, unknown> : {};
    usage = normalizeUsage(obj.usage) ?? usage;
    const choices = Array.isArray(obj.choices) ? obj.choices : [];
    for (const choice of choices) {
      const delta = (choice as Record<string, unknown>).delta as Record<string, unknown> | undefined;
      output += stringifyContent(delta?.content);
      const calls = Array.isArray(delta?.tool_calls) ? delta.tool_calls : Array.isArray(delta?.toolCalls) ? delta.toolCalls : [];
      for (const rawCall of calls) {
        const call = rawCall as Record<string, unknown>;
        const index = typeof call.index === "number" ? call.index : toolCallParts.size;
        const fn = call.function as Record<string, unknown> | undefined;
        const current = toolCallParts.get(index) ?? { id: "", name: "", args: "" };
        toolCallParts.set(index, {
          id: typeof call.id === "string" ? call.id : current.id,
          name: typeof fn?.name === "string" ? fn.name : current.name,
          args: current.args + (typeof fn?.arguments === "string" ? fn.arguments : ""),
        });
      }
    }
  }, () => emptyLlmEnd(output, [...toolCallParts.values()].map((call) => ({ id: call.id, name: call.name, args: parseToolArgs(call.args) })), usage), gateLlmEndData);
}

function captureResponseStream(model: string, messages: ReturnType<typeof normalizeMessages>, metadata: CallbackMetadata | null, stream: AsyncIterable<unknown>): AsyncIterable<unknown> {
  let output = "";
  let usage: LlmEndData["usage"] = null;
  const toolCallParts = new Map<string, { id: string; name: string; args: string }>();
  return captureLlmAsyncIterable(getHandler, { model, messages, metadata }, stream, (chunk) => {
    const obj = chunk && typeof chunk === "object" ? chunk as Record<string, unknown> : {};
    usage = normalizeUsage(obj.usage) ?? usage;
    if (obj.type === "response.output_text.delta" && typeof obj.delta === "string") output += obj.delta;
    if (typeof obj.output_text === "string") output += obj.output_text;
    collectResponseStreamToolCall(obj, toolCallParts);
  }, () => emptyLlmEnd(output, [...toolCallParts.values()].map((call) => ({ id: call.id, name: call.name, args: parseToolArgs(call.args) })), usage), gateLlmEndData);
}

function extractChatCompletion(result: unknown): LlmEndData {
  if (isAsyncIterable(result)) return emptyLlmEnd();
  const obj = result && typeof result === "object" ? result as Record<string, unknown> : {};
  const choices = Array.isArray(obj.choices) ? obj.choices : [];
  const first = choices[0] && typeof choices[0] === "object" ? choices[0] as Record<string, unknown> : {};
  const message = first.message && typeof first.message === "object" ? first.message as Record<string, unknown> : {};
  const toolCalls = normalizeOpenAIToolCalls(message.tool_calls ?? message.toolCalls);
  return emptyLlmEnd(stringifyContent(message.content), toolCalls, normalizeUsage(obj.usage));
}

function extractResponse(result: unknown): LlmEndData {
  if (isAsyncIterable(result)) return emptyLlmEnd();
  const obj = result && typeof result === "object" ? result as Record<string, unknown> : {};
  return emptyLlmEnd(typeof obj.output_text === "string" ? obj.output_text : stringifyContent(obj.output), normalizeResponseToolCalls(obj.output), normalizeUsage(obj.usage));
}

function normalizeOpenAIToolCalls(raw: unknown): ToolCallRecord[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((call) => {
    const obj = call && typeof call === "object" ? call as Record<string, unknown> : {};
    const fn = obj.function && typeof obj.function === "object" ? obj.function as Record<string, unknown> : {};
    return { id: String(obj.id ?? ""), name: String(fn.name ?? obj.name ?? ""), args: parseToolArgs(fn.arguments ?? obj.arguments ?? obj.args) };
  });
}

function normalizeResponseToolCalls(raw: unknown): ToolCallRecord[] {
  if (!Array.isArray(raw)) return [];
  return raw.flatMap((item) => {
    const obj = item && typeof item === "object" ? item as Record<string, unknown> : {};
    if (obj.type === "message" && Array.isArray(obj.content)) {
      return obj.content.flatMap((part) => normalizeResponseToolCalls([part]));
    }
    if (obj.type !== "function_call" && obj.type !== "tool_call") return [];
    return [{ id: String(obj.call_id ?? obj.id ?? ""), name: String(obj.name ?? ""), args: parseToolArgs(obj.arguments) }];
  });
}

function collectResponseStreamToolCall(obj: Record<string, unknown>, toolCallParts: Map<string, { id: string; name: string; args: string }>): void {
  const item = obj.item && typeof obj.item === "object" ? obj.item as Record<string, unknown> : null;
  if (item && (item.type === "function_call" || item.type === "tool_call")) {
    const key = String(item.id ?? item.call_id ?? obj.output_index ?? toolCallParts.size);
    const current = toolCallParts.get(key) ?? { id: "", name: "", args: "" };
    toolCallParts.set(key, {
      id: String(item.call_id ?? item.id ?? current.id),
      name: String(item.name ?? current.name),
      args: typeof item.arguments === "string" ? item.arguments : current.args,
    });
  }

  if (obj.type !== "response.function_call_arguments.delta" || typeof obj.delta !== "string") return;
  const key = String(obj.item_id ?? obj.output_index ?? toolCallParts.size);
  const current = toolCallParts.get(key) ?? { id: "", name: "", args: "" };
  toolCallParts.set(key, { ...current, args: current.args + obj.delta });
}

function integrationMetadata(metadata: CallbackMetadata | null | undefined, operation: string): CallbackMetadata {
  return { ...(metadata ?? {}), adrianIntegration: "openai", operation };
}

function isAsyncIterable(value: unknown): value is AsyncIterable<unknown> {
  return Boolean(value && typeof value === "object" && Symbol.asyncIterator in value);
}

export {
  AdrianPolicyBlockedError,
  BLOCKED_TOOL_MESSAGE,
  init,
  shutdown,
  getHandler,
  getWebSocketClient,
  version,
  __version__,
} from "@secureagentics/adrian";

export type { EventData, InitOptions } from "@secureagentics/adrian";
