import { randomUUID } from "node:crypto";
import type { AdrianCallbackHandler } from "../handler.js";
import { runWithInvocationId } from "../context.js";
import { shouldHalt, type WebSocketClient } from "../ws.js";
import { currentConfig } from "../config.js";

let patched = false;
const CALLBACK_METHODS = ["invoke", "stream", "batch", "ainvoke", "astream"] as const;
const GRAPH_METHODS = ["invoke", "stream", "ainvoke", "astream"] as const;
const TOOL_NODE_METHODS = ["invoke", "ainvoke"] as const;

export async function autoInstrument(getHandler: () => AdrianCallbackHandler | null, getWebSocketClient: () => WebSocketClient | null): Promise<void> {
  if (patched) return;
  patched = true;
  await Promise.allSettled([
    patchRunnable(getHandler),
    patchBaseChatModel(getHandler),
    patchLangGraph(getHandler),
    patchToolNode(getHandler, getWebSocketClient),
  ]);
}

function injectCallbacks(config: unknown, handler: AdrianCallbackHandler | null): unknown {
  if (!handler) return config ?? {};
  const next = { ...((config && typeof config === "object") ? config as Record<string, unknown> : {}) };
  const callbacks = next.callbacks;
  if (Array.isArray(callbacks)) {
    if (!callbacks.some((cb) => cb?.constructor?.name === "AdrianCallbackHandler")) next.callbacks = [handler, ...callbacks];
  } else if (callbacks) {
    next.callbacks = [handler, callbacks];
  } else {
    next.callbacks = [handler];
  }
  return next;
}

async function patchRunnable(getHandler: () => AdrianCallbackHandler | null): Promise<void> {
  const mod = await importOptional("@langchain/core/runnables");
  const proto = (mod?.Runnable as { prototype?: Record<string, unknown>; _adrianPatched?: boolean } | undefined)?.prototype;
  if (!proto || (mod.Runnable as { _adrianPatched?: boolean })._adrianPatched) return;
  for (const name of CALLBACK_METHODS) {
    const original = proto[name];
    if (typeof original !== "function") continue;
    proto[name] = function patchedInvoke(input: unknown, config?: unknown, ...rest: unknown[]) {
      return original.call(this, input, injectCallbacks(config, getHandler()), ...rest);
    };
  }
  (mod.Runnable as { _adrianPatched?: boolean })._adrianPatched = true;
}

async function patchBaseChatModel(getHandler: () => AdrianCallbackHandler | null): Promise<void> {
  const mod = await importOptional("@langchain/core/language_models/chat_models");
  const proto = (mod?.BaseChatModel as { prototype?: Record<string, unknown>; _adrianPatched?: boolean } | undefined)?.prototype;
  if (!proto || (mod.BaseChatModel as { _adrianPatched?: boolean })._adrianPatched) return;
  for (const name of CALLBACK_METHODS) {
    const original = proto[name];
    if (typeof original !== "function") continue;
    proto[name] = function patchedChatInvoke(input: unknown, config?: unknown, ...rest: unknown[]) {
      return original.call(this, input, injectCallbacks(config, getHandler()), ...rest);
    };
  }
  (mod.BaseChatModel as { _adrianPatched?: boolean })._adrianPatched = true;
}

async function patchLangGraph(getHandler: () => AdrianCallbackHandler | null): Promise<void> {
  const mod = await importOptional("@langchain/langgraph");
  const graphClasses = [mod?.CompiledStateGraph, mod?.StateGraph, mod?.Pregel].filter(Boolean) as Array<{ prototype?: Record<string, unknown>; _adrianPatched?: boolean }>;
  for (const cls of graphClasses) {
    const proto = cls.prototype;
    if (!proto || cls._adrianPatched) continue;
    for (const name of GRAPH_METHODS) {
      const original = proto[name];
      if (typeof original !== "function") continue;
      proto[name] = function patchedGraphInvoke(input: unknown, config?: unknown, ...rest: unknown[]) {
        const run = () => original.call(this, input, injectCallbacks(config, getHandler()), ...rest);
        return runWithInvocationId(randomUUID(), run);
      };
    }
    cls._adrianPatched = true;
  }
}

async function patchToolNode(getHandler: () => AdrianCallbackHandler | null, getWebSocketClient: () => WebSocketClient | null): Promise<void> {
  const mod = await importOptional("@langchain/langgraph/prebuilt");
  const cls = mod?.ToolNode as { prototype?: Record<string, unknown>; _adrianPatched?: boolean } | undefined;
  const proto = cls?.prototype;
  if (!cls || !proto || cls._adrianPatched) return;
  for (const name of TOOL_NODE_METHODS) {
    const original = proto[name];
    if (typeof original !== "function") continue;
    proto[name] = async function patchedToolNodeInvoke(input: unknown, config?: unknown, ...rest: unknown[]) {
      const nextConfig = injectCallbacks(config, getHandler());
      const blockedResponse = await blockedToolNodeResponse(input, getWebSocketClient());
      if (blockedResponse) return blockedResponse;
      return original.call(this, input, nextConfig, ...rest);
    };
  }
  cls._adrianPatched = true;
}

export async function blockedToolNodeResponse(input: unknown, ws: WebSocketClient | null): Promise<{ messages: Array<Record<string, string>> } | null> {
  if (!ws) return null;
  const cfg = currentConfig();
  const policyReady = await ws.waitForPolicyReady(cfg?.blockTimeout ?? 30);
  if (!policyReady || !ws.policyActive()) return null;
  const toolCalls = extractToolCalls(input);
  if (toolCalls.length === 0) return null;
  if (toolCalls.some((call) => !call.id)) return buildBlockedResponse(toolCalls);

  const timeout = ws.blockTimeout(cfg?.blockTimeout ?? 30);
  const verdicts = await Promise.all(toolCalls.map((call) => ws.waitForToolCallVerdict(call.id, timeout)));
  if (verdicts.some((verdict) => !verdict || shouldHalt(verdict))) return buildBlockedResponse(toolCalls);
  return null;
}

export function extractToolCalls(input: unknown): Array<{ id: string; name: string }> {
  const messages = Array.isArray(input) ? input : (input && typeof input === "object" ? (input as Record<string, unknown>).messages : []);
  if (!Array.isArray(messages)) return [];
  for (const message of [...messages].reverse()) {
    const calls = message && typeof message === "object" ? (message as Record<string, unknown>).tool_calls ?? (message as Record<string, unknown>).toolCalls : null;
    if (Array.isArray(calls)) return calls.map((call) => ({ id: String((call as Record<string, unknown>).id ?? ""), name: String((call as Record<string, unknown>).name ?? "") }));
  }
  return [];
}

export function buildBlockedResponse(toolCalls: Array<{ id: string; name: string }>): { messages: Array<Record<string, string>> } {
  return { messages: toolCalls.map((call) => ({ content: "[BLOCKED by security policy]", tool_call_id: call.id, name: call.name, type: "tool" })) };
}

async function importOptional(specifier: string): Promise<any | null> {
  try {
    return await import(specifier);
  } catch {
    return null;
  }
}
