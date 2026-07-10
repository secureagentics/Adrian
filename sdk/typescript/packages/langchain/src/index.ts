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
import type { CallbackMetadata, ChatMessage, LlmEndData, ToolCallRecord } from "@secureagentics/adrian";
import {
  captureLlmAsyncIterable,
  captureLlmCall,
  captureLlmExecute,
  emptyLlmEnd,
  gateLlmEndData,
  normalizeUsage,
  parseToolArgs,
  stringifyContent,
} from "@secureagentics/adrian/capture";

export interface AdrianOptions {
  metadata?: CallbackMetadata | null;
}

export interface ToolCaptureOptions {
  metadata?: CallbackMetadata | null;
  parentRunId?: string;
}

export interface ToolCallLike {
  id?: string;
  name?: string;
  args?: unknown;
  index?: number;
}

type LangChainExecute = (input: unknown, config?: unknown, ...rest: unknown[]) => unknown;
type LangChainToolPart = { id: string; name: string; args: string };

const RUNNABLE_METHODS = new Set(["invoke", "stream"]);
const RUNNABLE_DERIVATION_METHODS = new Set(["bind", "bindTools", "withConfig", "pipe"]);
const TOOL_METHODS = new Set(["invoke", "call"]);

/** Wrap manual LangChain tool execution so Adrian captures tool events. */
export async function captureTool<T>(
  toolCall: ToolCallLike,
  execute: () => T | Promise<T>,
  options: ToolCaptureOptions = {},
): Promise<T> {
  const handler = getHandler();
  if (!handler) return execute();

  const runId = randomUUID();
  const toolName = String(toolCall.name ?? "unknown");
  const toolCallId = String(toolCall.id ?? "");
  const input = stringifyContent(toolCall.args);
  const metadata = integrationMetadata(options.metadata, "langchain.tool_call");

  const gate = await gateToolCallIds(toolCallId ? [toolCallId] : [], getWebSocketClient(), currentConfig()?.blockTimeout ?? 30);
  const invocationId = getInvocationId();
  const run = async () => {
    await handler.handleToolStart({ name: toolName }, input, runId, options.parentRunId, { metadata, toolCallId });
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

/** Wrap LangChain tool objects or named tool maps so Adrian can gate execution. */
export function adrianTools<T>(tools: T, options: ToolCaptureOptions = {}): T {
  if (Array.isArray(tools)) return tools.map((tool) => wrapLangChainTool(tool, options)) as T;
  if (!tools || typeof tools !== "object") return tools;

  return Object.fromEntries(Object.entries(tools).map(([name, tool]) => {
    if (!tool || typeof tool !== "object") return [name, tool];
    return [name, wrapLangChainTool(tool, options, name)];
  })) as T;
}

/** Public entry: `adrian.langchain(modelOrTools)`. */
function wrapLangChain<T>(target: T, options: AdrianOptions = {}): T {
  if (Array.isArray(target)) {
    return adrianTools(target, options);
  }
  if (!target || typeof target !== "object") return target;

  const record = target as Record<PropertyKey, unknown>;
  if (typeof record.stream === "function" || typeof record.invoke === "function") {
    return instrumentRunnable(record, options) as T;
  }

  return adrianTools(target, options);
}

export function langchain<T>(target: T, options: AdrianOptions = {}): T {
  return wrapLangChain(target, options);
}

/** Proxy LangChain runnables so `invoke` and `stream` become captured LLM calls. */
function instrumentRunnable<T extends Record<PropertyKey, unknown>>(runnable: T, options: AdrianOptions): T {
  return new Proxy(runnable, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (RUNNABLE_DERIVATION_METHODS.has(String(prop)) && typeof value === "function") {
        return function adrianLangChainDerivedRunnable(this: unknown, ...args: unknown[]) {
          const nextArgs = prop === "bindTools" && args.length > 0 ? [adrianTools(args[0], options), ...args.slice(1)] : args;
          const result = value.call(target, ...nextArgs);
          return result && typeof result === "object" ? instrumentRunnable(result as Record<PropertyKey, unknown>, options) : result;
        };
      }

      if (!RUNNABLE_METHODS.has(String(prop)) || typeof value !== "function") return value;

      return function adrianLangChainRunnable(this: unknown, input: unknown, config?: unknown, ...rest: unknown[]) {
        const operation = `langchain.${String(prop)}`;
        return captureLangChainCall(operation, () => value.call(this, input, config, ...rest), target, input, options);
      };
    },
  });
}

function captureLangChainCall(
  operation: string,
  execute: () => unknown,
  runnable: Record<PropertyKey, unknown>,
  input: unknown,
  options: AdrianOptions,
): unknown {
  const model = extractModelName(runnable);
  const messages = normalizeLangChainMessages(input);
  const metadata = integrationMetadata(options.metadata, operation);

  if (operation.endsWith(".stream")) {
    return captureLlmExecute(getHandler, { model, messages, metadata }, async () => {
      const result = await Promise.resolve(execute());
      if (isAsyncIterable(result)) return captureLangChainStream(model, messages, metadata, result);
      return result;
    });
  }

  return captureLlmCall(getHandler, { model, messages, metadata }, () => Promise.resolve(execute()), extractLangChainResult, gateLlmEndData);
}

function wrapLangChainTool<T>(tool: T, options: ToolCaptureOptions, fallbackName?: string): T {
  if (!tool || typeof tool !== "object") return tool;

  return new Proxy(tool as Record<PropertyKey, unknown>, {
    get(target, prop, receiver) {
      const value = Reflect.get(target, prop, receiver);
      if (!TOOL_METHODS.has(String(prop)) || typeof value !== "function") return value;

      return function adrianLangChainTool(this: unknown, input: unknown, config?: unknown, ...rest: unknown[]) {
        const toolCall = extractToolCall(target, input, config, fallbackName);
        return captureTool(toolCall, () => (value as LangChainExecute).call(this, input, config, ...rest), options);
      };
    },
  }) as T;
}

/** Aggregate LangChain stream chunks into one paired LLM event at the end. */
function captureLangChainStream(model: string, messages: ChatMessage[], metadata: CallbackMetadata, stream: AsyncIterable<unknown>): AsyncIterable<unknown> {
  const outputChunks: string[] = [];
  let usage: LlmEndData["usage"] = null;
  const toolCallParts = new Map<string, LangChainToolPart>();

  return captureLlmAsyncIterable(getHandler, { model, messages, metadata }, stream, (chunk) => {
    const obj = asRecord(chunk);
    collectContent(obj, outputChunks);
    usage = extractUsage(obj) ?? usage;

    for (const call of normalizeToolCallArray(obj.tool_call_chunks ?? obj.toolCalls ?? obj.tool_calls)) {
      const key = call.index !== undefined ? String(call.index) : call.id || call.name || String(toolCallParts.size);
      const current = toolCallParts.get(key) ?? { id: "", name: "", args: "" };
      toolCallParts.set(key, {
        id: call.id || current.id,
        name: call.name || current.name,
        args: current.args + stringifyContent(call.args),
      });
    }
  }, () => emptyLlmEnd(
    outputChunks.join(""),
    [...toolCallParts.values()].map((call) => ({ id: call.id, name: call.name, args: parseToolArgs(call.args) })),
    usage,
  ), gateLlmEndData);
}

/** Map a completed LangChain result into Adrian LLM end data. */
function extractLangChainResult(result: unknown): LlmEndData {
  if (isAsyncIterable(result)) return emptyLlmEnd();
  if (typeof result === "string") return emptyLlmEnd(result);

  const obj = asRecord(result);
  const output = stringifyContent(obj.content ?? obj.text ?? "");
  const toolCalls = normalizeLangChainToolCalls(obj.tool_calls ?? obj.toolCalls);
  return emptyLlmEnd(output, toolCalls, extractUsage(obj));
}

function normalizeLangChainToolCalls(raw: unknown): ToolCallRecord[] {
  return normalizeToolCallArray(raw).map((call) => ({
    id: String(call.id ?? ""),
    name: String(call.name ?? ""),
    args: parseToolArgs(call.args),
  }));
}

function normalizeToolCallArray(raw: unknown): ToolCallLike[] {
  if (!Array.isArray(raw)) return [];
  return raw.map((call) => {
    const obj = asRecord(call);
    return {
      id: String(obj.id ?? obj.toolCallId ?? ""),
      name: String(obj.name ?? obj.toolName ?? ""),
      args: obj.args ?? obj.arguments ?? "",
      index: typeof obj.index === "number" ? obj.index : undefined,
    };
  });
}

function normalizeLangChainMessages(input: unknown): ChatMessage[] {
  if (typeof input === "string") return [{ role: "user", content: input }];
  if (!Array.isArray(input)) return [messageFromLangChain(input)];
  return input.map(messageFromLangChain);
}

function messageFromLangChain(message: unknown): ChatMessage {
  const obj = asRecord(message);
  const role = normalizeRole(String(obj.role ?? obj.type ?? callMaybe(obj._getType) ?? "user"));
  return { role, content: stringifyContent(obj.content ?? obj.text ?? message) };
}

function normalizeRole(role: string): string {
  if (role === "human") return "user";
  if (role === "ai") return "assistant";
  return role;
}

function extractToolCall(tool: Record<PropertyKey, unknown>, input: unknown, config: unknown, fallbackName?: string): ToolCallLike {
  const inputObj = asRecord(input);
  const configObj = asRecord(config);
  const state = asRecord(configObj.state);
  const langGraphToolCall = asRecord(state.lg_tool_call);
  return {
    id: String(inputObj.id ?? configObj.toolCallId ?? langGraphToolCall.id ?? ""),
    name: String(inputObj.name ?? langGraphToolCall.name ?? tool.name ?? fallbackName ?? "unknown"),
    args: inputObj.args ?? langGraphToolCall.args ?? inputObj.arguments ?? input,
  };
}

function extractModelName(runnable: Record<PropertyKey, unknown>): string {
  const kwargs = asRecord(runnable.kwargs);
  const serialized = asRecord(runnable.lc_kwargs);
  return String(
    runnable.modelName ??
    runnable.model ??
    runnable.modelId ??
    kwargs.model ??
    kwargs.modelName ??
    serialized.model ??
    runnable.lc_name ??
    runnable.constructor?.name ??
    "langchain",
  );
}

function extractUsage(obj: Record<PropertyKey, unknown>): LlmEndData["usage"] {
  const usage = obj.usage_metadata ?? asRecord(obj.response_metadata).tokenUsage ?? asRecord(obj.response_metadata).usage;
  return normalizeUsage(usage, ["input_tokens", "promptTokens", "inputTokens"], ["output_tokens", "completionTokens", "outputTokens"]);
}

function collectContent(obj: Record<PropertyKey, unknown>, outputChunks: string[]): void {
  if (typeof obj.content === "string") outputChunks.push(obj.content);
  if (typeof obj.text === "string") outputChunks.push(obj.text);
}

function integrationMetadata(metadata: CallbackMetadata | null | undefined, operation: string): CallbackMetadata {
  return { ...(metadata ?? {}), adrianIntegration: "langchain", operation };
}

function asRecord(value: unknown): Record<PropertyKey, unknown> {
  return value && typeof value === "object" ? value as Record<PropertyKey, unknown> : {};
}

function callMaybe(value: unknown): unknown {
  return typeof value === "function" ? value() : undefined;
}

function isAsyncIterable(value: unknown): value is AsyncIterable<unknown> {
  return Boolean(value && typeof value === "object" && Symbol.asyncIterator in value);
}

/**
 * Unified Adrian namespace for LangChain apps.
 * Prefer `import { adrian } from "@secureagentics/adrian-langchain"` over named exports.
 */
export const adrian = {
  init,
  shutdown,
  getHandler,
  getWebSocketClient,
  version,
  __version__: __version__,
  langchain: wrapLangChain,
  adrianTools,
  captureTool,
};

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
