import { randomUUID } from "node:crypto";
import { currentConfig } from "../config.js";
import type { AdrianCallbackHandler } from "../handler.js";
import { runWithInvocationId } from "../context.js";
import { assertToolCallsAllowed } from "../policy.js";
import { getWebSocketClient } from "../registry.js";
import type { CallbackMetadata, ChatMessage, LlmEndData, TokenUsage, ToolArgs, ToolCallRecord } from "../types.js";

/** Gate tool calls after the paired LLM event has been emitted (maps tool-call ids on the WS client). */
export async function gateLlmEndData(end: LlmEndData): Promise<void> {
  await assertToolCallsAllowed(
    end.toolCalls.map((call) => call.id),
    getWebSocketClient(),
    currentConfig()?.blockTimeout ?? 30,
  );
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
  return runWithInvocationId(randomUUID(), async () => {
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
  });
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
  const invocationId = randomUUID();

  async function* wrapped(): AsyncGenerator<T> {
    await handler?.handleChatModelStart({ name: input.model }, [input.messages], runId, input.parentRunId, { metadata: input.metadata });
    yield* runWithInvocationId(invocationId, async function* () {
      let emitted = false;
      let failed = false;
      try {
        for await (const chunk of iterable) {
          aggregate(chunk);
          yield chunk;
        }
        emitted = true;
        const endData = await extractOutput();
        await handler?.handleLLMEnd(endData, runId);
        await afterPairedEmit?.(endData);
      } catch (error) {
        failed = true;
        await handler?.handleLLMError(error, runId);
        throw error;
      } finally {
        if (!emitted && !failed) {
          const endData = await extractOutput();
          await handler?.handleLLMEnd(endData, runId);
          await afterPairedEmit?.(endData);
        }
      }
    });
  }

  return wrapped();
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
