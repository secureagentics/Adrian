// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { randomUUID } from "node:crypto";
import type { AdrianCallbackHandler } from "../handler.js";
import { getInvocationId, runWithInvocationId } from "../context.js";
import type { CallbackMetadata, ChatMessage, LlmEndData, TokenUsage, ToolArgs, ToolCallRecord } from "../types.js";

/** LLM end tool-call metadata is informational and never blockable. */
export async function gateLlmEndData(_end: LlmEndData): Promise<void> {
}

export interface LlmCaptureInput {
  model: string;
  messages: ChatMessage[];
  metadata?: CallbackMetadata | null;
  parentRunId?: string;
}

export async function captureLlmCall<T>(
  getHandler: () => AdrianCallbackHandler | null,
  input: LlmCaptureInput,
  execute: () => Promise<T>,
  extractOutput: (result: T) => LlmEndData | Promise<LlmEndData>,
  afterPairedEmit?: (end: LlmEndData) => void | Promise<void>,
): Promise<T> {
  const handler = getHandler();
  if (!handler) return execute();

  const runId = randomUUID();
  const invocationId = getInvocationId();
  const run = async () => {
    await handler.handleChatModelStart({ name: input.model }, [input.messages], runId, input.parentRunId, { metadata: input.metadata });
    try {
      const result = await execute();
      const endData = await extractOutput(result);
      await handler.handleLLMEnd(endData, runId);
      await afterPairedEmit?.(endData);
      return result;
    } catch (error) {
      await handler.handleLLMError(error, runId);
      throw error;
    }
  };
  return invocationId === null ? run() : runWithInvocationId(invocationId, run);
}

/** Wrap an LLM call that may fail before returning (e.g. streaming create). Records start+error, then re-throws. */
export async function captureLlmExecute<T>(
  getHandler: () => AdrianCallbackHandler | null,
  input: LlmCaptureInput,
  execute: () => Promise<T>,
): Promise<T> {
  try {
    return await execute();
  } catch (error) {
    const handler = getHandler();
    if (handler) {
      const runId = randomUUID();
      const invocationId = getInvocationId();
      const run = async () => {
        await handler.handleChatModelStart({ name: input.model }, [input.messages], runId, input.parentRunId, { metadata: input.metadata });
        await handler.handleLLMError(error, runId);
      };
      await (invocationId === null ? run() : runWithInvocationId(invocationId, run));
    }
    throw error;
  }
}

export function captureLlmAsyncIterable<T>(
  getHandler: () => AdrianCallbackHandler | null,
  input: LlmCaptureInput,
  iterable: AsyncIterable<T>,
  aggregate: (chunk: T) => void,
  extractOutput: () => LlmEndData | Promise<LlmEndData>,
  afterPairedEmit?: (end: LlmEndData) => void | Promise<void>,
): AsyncIterable<T> {
  const handler = getHandler();
  if (!handler) return iterable;

  const runId = randomUUID();
  const invocationId = getInvocationId();
  const activeHandler = handler;

  const createIterator = (): AsyncIterator<T> => {
    async function* gen(): AsyncGenerator<T> {
      await activeHandler.handleChatModelStart({ name: input.model }, [input.messages], runId, input.parentRunId, { metadata: input.metadata });

      const streamBody = async function* (): AsyncGenerator<T> {
        let emitted = false;
        let failed = false;
        try {
          for await (const chunk of iterable) {
            aggregate(chunk);
            yield chunk;
          }
          emitted = true;
          const endData = await extractOutput();
          await activeHandler.handleLLMEnd(endData, runId);
          await afterPairedEmit?.(endData);
        } catch (error) {
          failed = true;
          await activeHandler.handleLLMError(error, runId);
          throw error;
        } finally {
          if (!emitted && !failed) {
            const endData = await extractOutput();
            await activeHandler.handleLLMEnd(endData, runId);
            await afterPairedEmit?.(endData);
          }
        }
      };

      if (invocationId === null) {
        yield* streamBody();
      } else {
        yield* runWithInvocationId(invocationId, streamBody);
      }
    }
    return gen();
  };

  return preserveStreamSurface(iterable, createIterator);
}

/** Keep provider stream helpers (tee, toReadableStream, controller) while intercepting iteration. */
function preserveStreamSurface<T>(
  source: AsyncIterable<T>,
  createIterator: () => AsyncIterator<T>,
): AsyncIterable<T> {
  const iterable: AsyncIterable<T> = { [Symbol.asyncIterator]: createIterator };
  if (!source || typeof source !== "object") return iterable;

  const stream = source as Record<PropertyKey, unknown> & AsyncIterable<T>;
  return new Proxy(iterable, {
    get(_target, prop, receiver) {
      if (prop === Symbol.asyncIterator) return createIterator;
      if (prop === "tee") {
        return () => teeCapturingStream(createIterator).map((branch) => preserveStreamSurface(stream, branch));
      }
      const value = Reflect.get(stream, prop, stream);
      if (typeof value === "function") return value.bind(receiver);
      return value;
    },
  });
}

/** Split one capturing iterator into two branches without restarting capture. */
function teeCapturingStream<T>(
  createIterator: () => AsyncIterator<T>,
): [() => AsyncIterator<T>, () => AsyncIterator<T>] {
  const left: Array<Promise<IteratorResult<T>>> = [];
  const right: Array<Promise<IteratorResult<T>>> = [];
  const iterator = createIterator();

  const branchIterator = (queue: Array<Promise<IteratorResult<T>>>) => (): AsyncIterator<T> => ({
    next: () => {
      if (queue.length === 0) {
        const result = iterator.next();
        left.push(result);
        right.push(result);
      }
      return queue.shift()!;
    },
    return: (value) => iterator.return?.(value) ?? Promise.resolve({ done: true as const, value: undefined }),
    throw: (error) => iterator.throw?.(error) ?? Promise.reject(error),
  });

  return [branchIterator(left), branchIterator(right)];
}

export function normalizeMessages(input: unknown): ChatMessage[] {
  if (typeof input === "string") return [{ role: "user", content: input }];
  if (!Array.isArray(input)) return [];
  return input.map((message) => {
    const obj = message && typeof message === "object" ? message as Record<string, unknown> : {};
    return {
      role: String(obj.role),
      content: stringifyContent(obj.content ?? obj.text),
    };
  });
}

export function messagesFromPromptLike(args: Record<string, unknown>): ChatMessage[] {
  const system = args.instructions;
  const messages = normalizeMessages(args.messages);
  if (messages.length > 0) return prependSystem(system, messages);
  if (typeof args.input === "string") return prependSystem(system, [{ role: "user", content: args.input }]);
  const inputMessages = normalizeResponseInput(args.input);
  if (inputMessages.length > 0) return prependSystem(system, inputMessages);
  return prependSystem(system, []);
}

/** Normalise OpenAI Responses API `input` arrays (roles, tool calls, tool outputs). */
export function normalizeResponseInput(input: unknown): ChatMessage[] {
  if (!Array.isArray(input)) return [];
  const messages: ChatMessage[] = [];
  for (const item of input) {
    if (!item || typeof item !== "object") continue;
    const obj = item as Record<string, unknown>;
    const type = String(obj.type);

    if (type === "function_call" || type === "tool_call") {
      const name = String(obj.name);
      const args = typeof obj.arguments === "string" ? obj.arguments : stringifyJson(obj.arguments);
      messages.push({ role: "assistant", content: `[tool_call:${name}] ${args}` });
      continue;
    }
    if (type === "function_call_output") {
      messages.push({ role: "tool", content: String(obj.output) });
      continue;
    }

    const role = String(obj.role);
    if (!role) continue;
    messages.push({
      role: role === "developer" ? "system" : role,
      content: stringifyContent(obj.content ?? obj.text),
    });
  }
  return messages;
}

export function stringifyContent(value: unknown): string {
  if (typeof value === "string") return value;
  if (Array.isArray(value)) {
    return value.map((part) => {
      if (typeof part === "string") return part;
      if (part && typeof part === "object") {
        const obj = part as Record<string, unknown>;
        if (typeof obj.text === "string") return obj.text;
        if (typeof obj.content === "string") return obj.content;
      }
      return stringifyJson(part);
    }).join("");
  }
  return stringifyJson(value);
}

export function normalizeUsage(usage: unknown, promptKeys = ["promptTokens", "prompt_tokens", "input_tokens"], completionKeys = ["completionTokens", "completion_tokens", "output_tokens"]): TokenUsage | null {
  if (!usage || typeof usage !== "object") return null;
  const obj = usage as Record<string, unknown>;
  const promptTokens = numberFromKeys(obj, promptKeys);
  const completionTokens = numberFromKeys(obj, completionKeys);
  const totalTokens = numberFromKeys(obj, ["totalTokens", "total_tokens"]) ?? ((promptTokens ?? 0) + (completionTokens ?? 0));
  if (promptTokens === null && completionTokens === null && totalTokens === 0) return null;
  return { promptTokens: promptTokens ?? 0, completionTokens: completionTokens ?? 0, totalTokens };
}

export function parseToolArgs(value: unknown): ToolArgs {
  if (!value) return {};
  if (typeof value === "object" && !Array.isArray(value)) return value as ToolArgs;
  if (typeof value !== "string") return {};
  try {
    const parsed = JSON.parse(value);
    return parsed && typeof parsed === "object" && !Array.isArray(parsed) ? parsed as ToolArgs : {};
  } catch {
    return {};
  }
}

export function emptyLlmEnd(output = "", toolCalls: ToolCallRecord[] = [], usage: TokenUsage | null = null): LlmEndData {
  return { output, toolCalls, usage };
}

function prependSystem(system: unknown, messages: ChatMessage[]): ChatMessage[] {
  return typeof system === "string" && system.length > 0 ? [{ role: "system", content: system }, ...messages] : messages;
}

function numberFromKeys(obj: Record<string, unknown>, keys: string[]): number | null {
  for (const key of keys) {
    const value = obj[key];
    if (typeof value === "number" && Number.isFinite(value)) return value;
  }
  return null;
}

function stringifyJson(value: unknown): string {
  if (value === null || value === undefined) return "";
  try {
    return JSON.stringify(value);
  } catch {
    return String(value);
  }
}
