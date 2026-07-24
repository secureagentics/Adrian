// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { randomUUID } from "node:crypto";
import {
  BLOCKED_TOOL_MESSAGE,
  currentConfig,
  gateToolCallIds,
  getHandler,
  getInvocationId,
  getWebSocketClient,
  init,
  runWithInvocationId,
  shutdown,
  version,
  __version__,
} from "@secureagentics/adrian";
import type { CallbackMetadata, EventData, InitOptions, LlmEndData, ToolCallRecord } from "@secureagentics/adrian";
import {
  captureLlmAsyncIterable,
  captureLlmCall,
  captureLlmExecute,
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

  const gate = await gateToolCallIds(toolCallId ? [toolCallId] : [], getWebSocketClient(), currentConfig()?.blockTimeout ?? 30);

  // Match Python: only inherit an invocation that was established upstream.
  const invocationId = getInvocationId();
  const run = async () => {
    await handler.handleToolStart({ name: toolName }, input, runId, options.parentRunId, { metadata, tool_call_id: toolCallId });
    if (gate.action === "block") {
      await handler.handleToolEnd(BLOCKED_TOOL_MESSAGE, runId);
      return BLOCKED_TOOL_MESSAGE as T;
    }
    try {
      const result = await execute();
      await handler.handleToolEnd(result, runId);
      return result;
    } catch (error) {
      await handler.handleToolError(error, runId);
      throw error;
    }
  };
  return invocationId === null ? run() : runWithInvocationId(invocationId, run);
}

/** Public entry: `adrian.openai(new OpenAI())`. */
function wrapOpenAI<T extends object>(client: T, options: AdrianOptions = {}): T {
  // Top-level proxy routes into chat.completions and responses.create.
  return new Proxy(client, {
    get(target, prop, receiver) {
      if (prop === "chat") return instrumentChat(Reflect.get(target, prop, receiver), options);
      if (prop === "responses") return instrumentResponses(Reflect.get(target, prop, receiver), options);
      return Reflect.get(target, prop, receiver);
    },
  });
}

/** Proxy `client.chat` → `completions.create`. */
function instrumentChat(chat: unknown, options: AdrianOptions): unknown {
  if (!chat || typeof chat !== "object") return chat;
  return new Proxy(chat as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      if (prop === "completions") return instrumentChatCompletions(Reflect.get(target, prop, receiver), options);
      return Reflect.get(target, prop, receiver);
    },
  });
}

/**
 * Intercept `chat.completions.create`.
 * Non-stream: one paired LLM event after the response resolves.
 * Stream: wrap the async iterable; emit one event when the stream ends.
 */
function instrumentChatCompletions(completions: unknown, options: AdrianOptions): unknown {
  if (!completions || typeof completions !== "object") return completions;
  return new Proxy(completions as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (prop !== "create" || typeof value !== "function") return value;
      return async function adrianOpenAIChatCreate(this: unknown, body: Record<string, unknown> = {}, ...rest: unknown[]) {
        const model = String(body.model || "openai");
        const metadata = integrationMetadata(options.metadata, "openai.chat.completions");
        const messages = normalizeMessages(body.messages);
        if (body.stream === true) {
          return captureLlmExecute(getHandler, { model, messages, metadata }, async () => {
            const result = await value.call(target, body, ...rest);
            if (isAsyncIterable(result)) return captureChatCompletionStream(model, messages, metadata, result);
            return result;
          });
        }
        // Non-stream path: core capture helper pairs handleChatModelStart/End around the call.
        return captureLlmCall(getHandler, { model, messages, metadata }, () => Promise.resolve(value.call(target, body, ...rest)), extractChatCompletion, gateLlmEndData);
      };
    },
  });
}

/**
 * Intercept `responses.create`.
 * Maps `input` / `instructions` into Adrian message shape via messagesFromPromptLike.
 */
function instrumentResponses(responses: unknown, options: AdrianOptions): unknown {
  if (!responses || typeof responses !== "object") return responses;
  return new Proxy(responses as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (prop !== "create" || typeof value !== "function") return value;
      return async function adrianOpenAIResponsesCreate(this: unknown, body: Record<string, unknown> = {}, ...rest: unknown[]) {
        const model = String(body.model ?? "openai");
        const metadata = integrationMetadata(options.metadata, "openai.responses");
        const messages = messagesFromPromptLike({
          input: body.input,
          instructions: body.instructions,
        });
        if (body.stream === true) {
          return captureLlmExecute(getHandler, { model, messages, metadata }, async () => {
            const result = await Promise.resolve(value.call(target, body, ...rest));

            if (isAsyncIterable(result)) return captureResponseStream(model, messages, metadata, result);
            return result;
          });
        }
        return captureLlmCall(getHandler, { model, messages, metadata }, () => Promise.resolve(value.call(target, body, ...rest)), extractResponse, gateLlmEndData);
      };
    },
  });
}

/** Aggregate Chat Completions stream chunks into one paired LLM event at the end. */
function captureChatCompletionStream(model: string, messages: ReturnType<typeof normalizeMessages>, metadata: CallbackMetadata | null, stream: AsyncIterable<unknown>): AsyncIterable<unknown> {
  let output = "";
  let usage: LlmEndData["usage"] = null;
  // OpenAI streams tool calls by index; merge partial deltas before emit.
  const toolCallParts = new Map<number, { id: string; name: string; args: string }>();
  return captureLlmAsyncIterable(getHandler, { model, messages, metadata }, stream, (chunk) => {
    const obj = chunk as Record<string, unknown>;
    // Usage arrives on the final chunk when stream_options.include_usage is set.
    usage = normalizeUsage(obj.usage) ?? usage;
    for (const choice of (obj.choices as any[] ?? [])) {
      const delta = choice.delta;
      output += stringifyContent(delta?.content);

      for (const call of (delta?.tool_calls ?? [])) {
        const fn = call.function;
        const current = toolCallParts.get(call.index) ?? { id: "", name: "", args: "" };
        toolCallParts.set(call.index, {
          id: call.id ?? current.id,
          name: fn?.name ?? current.name,
          args: current.args + (fn?.arguments ?? ""),
        });
      }
    }
  }, () => emptyLlmEnd(output, [...toolCallParts.values()].map((call) => ({ id: call.id, name: call.name, args: parseToolArgs(call.args) })), usage), gateLlmEndData);
}

/** Aggregate Responses API stream events into one paired LLM event at the end. */
function captureResponseStream(model: string, messages: ReturnType<typeof normalizeMessages>, metadata: CallbackMetadata | null, stream: AsyncIterable<unknown>): AsyncIterable<unknown> {
  let output = "";
  let usage: LlmEndData["usage"] = null;

  const toolCallParts = new Map<string, { id: string; name: string; args: string }>();

  return captureLlmAsyncIterable(
    getHandler,
    { model, messages, metadata },
    stream,
    (chunk) => {
      const obj = chunk as Record<string, unknown>;
      switch (obj.type) {
        case "response.output_text.delta":
          output += obj.delta;
          break;

        case "response.completed":
          usage = normalizeUsage(obj.usage) ?? usage;
          break;
      }
      collectResponseStreamToolCall(obj, toolCallParts);
    },
    () =>
      emptyLlmEnd(
        output,
        [...toolCallParts.values()].map((call) => ({
          id: call.id,
          name: call.name,
          args: parseToolArgs(call.args),
        })),
        usage,
      ),
    gateLlmEndData,
  );
}

/** Map a completed Chat Completions response object into Adrian LLM end data. */
function extractChatCompletion(result: unknown): LlmEndData {
  if (isAsyncIterable(result)) return emptyLlmEnd();
  const obj = result as Record<string, unknown>;
  const message = (obj.choices as Record<string, unknown>[])[0]
    ?.message as Record<string, unknown> | undefined;

  return emptyLlmEnd(
    stringifyContent(message?.content),
    normalizeOpenAIToolCalls(message?.tool_calls),
    normalizeUsage(obj.usage),
  );
}

/** Map a completed Responses API object into Adrian LLM end data. */
function extractResponse(result: unknown): LlmEndData {
  if (isAsyncIterable(result)) return emptyLlmEnd();
  const obj = result as Record<string, unknown>;

  return emptyLlmEnd(
    typeof obj.output_text === "string" ? obj.output_text : stringifyContent(obj.output),
    normalizeResponseToolCalls(obj.output),
    normalizeUsage(obj.usage),
  );
}

/** Normalise Chat Completions `message.tool_calls` into Adrian tool call records. */
function normalizeOpenAIToolCalls(raw: unknown): ToolCallRecord[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((call) => {
    const obj = call as Record<string, unknown>;
    const fn = obj.function as {name: string, arguments: string} | undefined;
    return { id: String(obj.id ?? ""), name: String(fn?.name ?? obj.name ?? ""), args: parseToolArgs(fn?.arguments) };
  });
}

/** Walk Responses `output` tree for function_call / tool_call items. */
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

/**
 * Responses streaming emits tool metadata and argument deltas in separate events.
 * Handles `response.output_item.added` items and `response.function_call_arguments.delta`.
 */
function collectResponseStreamToolCall(obj: Record<string, unknown>, toolCallParts: Map<string, { id: string; name: string; args: string }>): void {
  switch (obj.type) {
    case "response.output_item.added":
    case "response.output_item.done": {
      const item = obj.item as Record<string, unknown> | undefined;
      if (!item || (item.type !== "function_call" && item.type !== "tool_call")) break;
      const key = String(item.id ?? item.call_id ?? obj.output_index ?? toolCallParts.size);
      const current = toolCallParts.get(key) ?? { id: "", name: "", args: "" };
      toolCallParts.set(key, {
        id: String(item.call_id ?? item.id ?? current.id),
        name: String(item.name ?? current.name),
        args: typeof item.arguments === "string" ? item.arguments : current.args,
      });
      break;
    }

    case "response.function_call_arguments.delta": {
      if (typeof obj.delta !== "string") break;
      const key = String(obj.item_id ?? obj.output_index ?? toolCallParts.size);
      const current = toolCallParts.get(key) ?? { id: "", name: "", args: "" };
      toolCallParts.set(key, { ...current, args: current.args + obj.delta });
      break;
    }
  }
}

/** Tag events with provider integration metadata for downstream filtering. */
function integrationMetadata(metadata: CallbackMetadata | null | undefined, operation: string): CallbackMetadata {
  return { ...(metadata ?? {}), adrianIntegration: "openai", operation };
}

function isAsyncIterable(value: unknown): value is AsyncIterable<unknown> {
  return Boolean(value && typeof value === "object" && Symbol.asyncIterator in value);
}

/**
 * Unified Adrian namespace for OpenAI apps.
 * Prefer `import { adrian } from "@secureagentics/adrian-openai"` over named exports.
 */
export const adrian = {
  init,
  shutdown,
  getHandler,
  getWebSocketClient,
  version,
  __version__: __version__,
  openai: wrapOpenAI,
  captureTool,
};

export {
  AdrianPolicyBlockedError,
  BLOCKED_TOOL_MESSAGE,
  getHandler,
  getWebSocketClient,
  init,
  shutdown,
  version,
  __version__,
} from "@secureagentics/adrian";

export type { EventData, InitOptions };
