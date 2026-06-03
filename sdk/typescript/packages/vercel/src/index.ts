import { randomUUID } from "node:crypto";
import { assertToolCallsAllowed, currentConfig, getHandler, getWebSocketClient, runWithInvocationId } from "@secureagentics/adrian";
import type { CallbackMetadata, LlmEndData, ToolCallRecord } from "@secureagentics/adrian";
import { captureLlmCall, gateLlmEndData, emptyLlmEnd, messagesFromPromptLike, normalizeUsage, parseToolArgs, stringifyContent } from "@secureagentics/adrian/capture";

export interface AdrianOptions {
  metadata?: CallbackMetadata | null;
}

export interface ToolCallLike {
  toolCallId?: string;
  id?: string;
  toolName?: string;
  name?: string;
  args?: unknown;
}

export interface ToolCaptureOptions {
  metadata?: CallbackMetadata | null;
  parentRunId?: string;
}

type VercelToolExecute = (args: unknown, options?: unknown, ...rest: unknown[]) => unknown;

const VERCEL_METHODS = new Set(["generateText", "streamText", "generateObject", "streamObject"]);

/** Wrap a Vercel AI SDK module or tools object so Adrian captures LLM and tool events. */
export function adrian<T>(target: T, options: AdrianOptions = {}): T {
  if (!target || typeof target !== "object") return target;

  if ("generateText" in target || "streamText" in target) {
    return wrapAiModule(target as Record<PropertyKey, unknown>, options) as T;
  }

  const keys = Object.keys(target);
  if (keys.length > 0 && keys.every((k) => {
    const val = (target as Record<string, unknown>)[k];
    return val && typeof val === "object" && ("execute" in val || "description" in val);
  })) {
    return adrianTools(target as Record<string, unknown>, options) as T;
  }

  return target;
}

/** Wrap Vercel AI SDK tool definitions so Adrian captures tool execution events. */
export function adrianTools<T extends Record<string, unknown>>(tools: T, options: ToolCaptureOptions = {}): T {
  return Object.fromEntries(Object.entries(tools).map(([toolName, toolDef]) => {
    if (!toolDef || typeof toolDef !== "object") return [toolName, toolDef];
    const execute = (toolDef as { execute?: unknown }).execute;
    if (typeof execute !== "function") return [toolName, toolDef];

    return [toolName, {
      ...(toolDef as Record<string, unknown>),
      execute(this: unknown, args: unknown, executionOptions?: unknown, ...rest: unknown[]) {
        const toolCallId = extractToolCallId(executionOptions);
        return captureTool({
          toolCallId,
          toolName,
          args,
        }, () => (execute as VercelToolExecute).call(this, args, executionOptions, ...rest), options);
      },
    }];
  })) as T;
}

/** Wrap manual Vercel AI SDK tool execution so Adrian captures tool events. */
export async function captureTool<T>(
  toolCall: ToolCallLike,
  execute: () => T | Promise<T>,
  options: ToolCaptureOptions = {},
): Promise<T> {
  const handler = getHandler();
  if (!handler) return execute();

  const runId = randomUUID();
  const toolName = String(toolCall.toolName ?? toolCall.name ?? "unknown");
  const toolCallId = String(toolCall.toolCallId ?? toolCall.id ?? "");
  const input = stringifyContent(toolCall.args);
  const metadata = integrationMetadata(options.metadata, "vercel-ai.tool_call");

  await assertToolCallsAllowed(toolCallId ? [toolCallId] : [], getWebSocketClient(), currentConfig()?.blockTimeout ?? 30);

  return runWithInvocationId(randomUUID(), async () => {
    await handler.handleToolStart({ name: toolName }, input, runId, options.parentRunId, { metadata, toolCallId });
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

function wrapAiModule<T extends Record<PropertyKey, unknown>>(ai: T, options: AdrianOptions = {}): T {
  return new Proxy(ai, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (!VERCEL_METHODS.has(String(prop)) || typeof value !== "function") return value;
      return function adrianVercelAI(this: unknown, args: Record<string, unknown> = {}, ...rest: unknown[]) {
        return captureVercelCall(String(prop), () => value.call(this, args, ...rest), args, options);
      };
    },
  });
}

function captureVercelCall<T>(operation: string, execute: () => T, args: Record<string, unknown>, options: AdrianOptions): unknown {
  const model = extractModelName(args.model);
  const metadata = integrationMetadata(options.metadata, operation);
  const result = execute();

  if (operation.startsWith("stream")) {
    void Promise.resolve(result).then((resolved) => emitVercelStreamResult(model, args, metadata, resolved)).catch(() => undefined);
    return result;
  }

  return Promise.resolve(result).then((resolved) => captureLlmCall(getHandler, { model, messages: messagesFromPromptLike(args), metadata }, async () => resolved, extractVercelResult, gateLlmEndData));
}

async function emitVercelStreamResult(model: string, args: Record<string, unknown>, metadata: CallbackMetadata, result: unknown): Promise<void> {
  await captureLlmCall(getHandler, { model, messages: messagesFromPromptLike(args), metadata }, async () => result, async (streamResult) => {
    const obj = streamResult && typeof streamResult === "object" ? streamResult as Record<string, unknown> : {};
    const [text, toolCalls, usage] = await Promise.all([
      resolveMaybe(obj.text, ""),
      resolveMaybe(obj.toolCalls, []),
      resolveMaybe(obj.usage, null),
    ]);
    return emptyLlmEnd(typeof text === "string" ? text : stringifyContent(text), normalizeVercelToolCalls(toolCalls), normalizeVercelUsage(usage));
  }, gateLlmEndData);
}

function extractVercelResult(result: unknown): LlmEndData {
  const obj = result && typeof result === "object" ? result as Record<string, unknown> : {};
  const output = typeof obj.text === "string" ? obj.text : typeof obj.object !== "undefined" ? stringifyContent(obj.object) : "";
  return emptyLlmEnd(output, normalizeVercelToolCalls(obj.toolCalls), normalizeVercelUsage(obj.usage));
}

function normalizeVercelToolCalls(raw: unknown): ToolCallRecord[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((call) => {
    const obj = call && typeof call === "object" ? call as Record<string, unknown> : {};
    return {
      id: String(obj.toolCallId ?? obj.id ?? ""),
      name: String(obj.toolName ?? obj.name ?? ""),
      args: parseToolArgs(obj.args),
    };
  });
}

function normalizeVercelUsage(usage: unknown): LlmEndData["usage"] {
  return normalizeUsage(usage, ["promptTokens", "inputTokens"], ["completionTokens", "outputTokens"]);
}

async function resolveMaybe(value: unknown, fallback: unknown): Promise<unknown> {
  if (value === undefined || value === null) return fallback;
  return Promise.resolve(value);
}

function extractModelName(model: unknown): string {
  if (typeof model === "string") return model;
  if (model && typeof model === "object") {
    const obj = model as Record<string, unknown>;
    return String(obj.modelId ?? obj.model ?? obj.id ?? obj.name ?? "vercel-ai");
  }
  return "vercel-ai";
}

function extractToolCallId(executionOptions: unknown): string {
  if (!executionOptions || typeof executionOptions !== "object") return "";
  const obj = executionOptions as Record<string, unknown>;
  return String(obj.toolCallId ?? obj.id ?? "");
}

function integrationMetadata(metadata: CallbackMetadata | null | undefined, operation: string): CallbackMetadata {
  return { ...(metadata ?? {}), adrianIntegration: "vercel-ai", operation };
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
