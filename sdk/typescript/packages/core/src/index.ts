// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { resolveInitOptions, setConfig, type AdrianConfig, type InitOptions } from "./config.js";
import { AgentContextTracker } from "./context.js";
import { AdrianCallbackHandler } from "./handler.js";
import { JSONLHandler } from "./handlers/jsonl.js";
import { HookRegistry } from "./hooks.js";
import { patchMcpAdapters, mcpServers } from "./mcp.js";
import { EventPairBuffer } from "./pairing.js";
import { RedactingHandler } from "./pii/index.js";
import { getHandler, getWebSocketClient, setRuntime } from "./registry.js";
import { envAwareResolveSessionId } from "./sessionPersistence.js";
import { WebSocketClient } from "./ws.js";
import type { EventHandler, McpServer } from "./types.js";

declare const __ADRIAN_CORE_VERSION__: string | undefined;

// Inlined from package.json at build time (see tsup.config.ts); falls back
// to a dev marker when run unbuilt (tests, ts-node), where the define is absent.
export const version: string =
  typeof __ADRIAN_CORE_VERSION__ === "string" ? __ADRIAN_CORE_VERSION__ : "0.0.0-dev";
export const __version__ = version;

let hooks: HookRegistry | null = null;

export async function init(options: InitOptions = {}): Promise<void> {
  const resolved = resolveInitOptions(options);
  const sessionId = await envAwareResolveSessionId(options.sessionId);

  const config: AdrianConfig = {
    apiKey: resolved.apiKey,
    logFile: resolved.logFile,
    logLevel: options.logLevel ?? null,
    sessionId,
    wsUrl: resolved.wsUrl,
    blockTimeout: resolved.blockTimeout,
    onEvent: options.onEvent ?? null,
    onVerdict: options.onVerdict ?? null,
    onBlock: options.onBlock ?? null,
    onAudit: options.onAudit ?? null,
    onDisconnect: options.onDisconnect ?? null,
    onReconnect: options.onReconnect ?? null,
    onMcpServer: chainMcpServerCallback(options.onMcpServer ?? null),
    replayBufferFrames: resolved.replayBufferFrames,
  };
  setConfig(config);

  const handlerList: EventHandler[] = options.handlers ? [...options.handlers] : [new JSONLHandler(resolved.logFile)];
  let wsClient: WebSocketClient | null = null;
  if (!options.handlers && resolved.wsUrl) {
    if (!resolved.apiKey) console.warn("ADRIAN wsUrl is set but no apiKey was provided; the server will reject the connection.");
    wsClient = new WebSocketClient({
      url: resolved.wsUrl,
      sessionId,
      apiKey: resolved.apiKey ?? "",
      onDisconnect: config.onDisconnect,
      onReconnect: config.onReconnect,
      onLoginAck: sendMcpInventory,
      replayBufferFrames: config.replayBufferFrames,
    });
    handlerList.push(wsClient);
  }

  hooks = new HookRegistry();
  for (const eventHandler of handlerList.map((h) => new RedactingHandler(h))) hooks.register(eventHandler);
  const handler = new AdrianCallbackHandler({ pairBuffer: new EventPairBuffer(), contextTracker: new AgentContextTracker(), hooks, config });
  setRuntime(handler, wsClient);
  if (wsClient) {
    wsClient.handler = handler;
    wsClient.scheduleConnect();
  }

  await patchMcpAdapters();
}

export const adrian = {
  init,
  shutdown,
  getHandler,
  getWebSocketClient,
  version,
  __version__: version,
};

export async function shutdown(): Promise<void> {
  await hooks?.close();
  hooks = null;
  setRuntime(null, null);
  setConfig(null);
}

async function sendMcpInventory(): Promise<void> {
  await getWebSocketClient()?.sendMcpInventory(mcpServers());
}

function chainMcpServerCallback(userCallback: ((server: McpServer) => void | Promise<void>) | null) {
  return async (server: McpServer) => {
    await sendMcpInventory();
    await userCallback?.(server);
  };
}

export { AdrianCallbackHandler } from "./handler.js";
export { JSONLHandler } from "./handlers/jsonl.js";
export { HookRegistry } from "./hooks.js";
export { EventPairBuffer } from "./pairing.js";
export { AgentContextTracker, getInvocationId, runWithInvocationId } from "./context.js";
export { deriveAgentId } from "./identity.js";
export { WebSocketClient, shouldHalt } from "./ws.js";
export { AdrianPolicyBlockedError, BLOCKED_TOOL_MESSAGE, assertToolCallsAllowed, gateToolCallIds } from "./policy.js";
export type { GateToolCallsReason, GateToolCallsResult } from "./policy.js";
export { mcpServers, registerMcpServer, registerMcpConnection } from "./mcp.js";
export { resolveSessionId, envAwareResolveSessionId } from "./sessionPersistence.js";
export { getHandler, getWebSocketClient } from "./registry.js";
export * from "./config.js";
export * from "./types.js";
export * from "./format/types.js";
export * from "./pii/index.js";
export * from "./proto/schema.js";
