import { setConfig, type AdrianConfig, type InitOptions } from "./config.js";
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

export const version = "1.0.0";
export const __version__ = version;

let hooks: HookRegistry | null = null;

export async function init(options: InitOptions = {}): Promise<void> {
  const apiKey = options.apiKey ?? process.env.ADRIAN_API_KEY ?? null;
  const logFile = process.env.ADRIAN_LOG_FILE ?? options.logFile ?? "events.jsonl";
  const wsUrl = process.env.ADRIAN_WS_URL ?? options.wsUrl ?? "ws://localhost:8080/ws";
  const sessionId = await envAwareResolveSessionId(options.sessionId ?? null);
  const blockTimeout = Number(process.env.ADRIAN_BLOCK_TIMEOUT ?? options.blockTimeout ?? 30);
  const replayBufferFrames = parseInt(process.env.ADRIAN_REPLAY_BUFFER_FRAMES ?? String(options.replayBufferFrames ?? 1000), 10);

  const config: AdrianConfig = {
    apiKey,
    logFile,
    logLevel: options.logLevel ?? null,
    sessionId,
    wsUrl,
    blockTimeout,
    onEvent: options.onEvent ?? null,
    onVerdict: options.onVerdict ?? null,
    onBlock: options.onBlock ?? null,
    onAudit: options.onAudit ?? null,
    onDisconnect: options.onDisconnect ?? null,
    onReconnect: options.onReconnect ?? null,
    onMcpServer: chainMcpServerCallback(options.onMcpServer ?? null),
    replayBufferFrames: Number.isFinite(replayBufferFrames) ? replayBufferFrames : 1000,
  };
  setConfig(config);

  const handlerList: EventHandler[] = options.handlers ? [...options.handlers] : [new JSONLHandler(logFile)];
  let wsClient: WebSocketClient | null = null;
  if (!options.handlers && wsUrl) {
    if (!apiKey) console.warn("ADRIAN wsUrl is set but no apiKey was provided; the server will reject the connection.");
    wsClient = new WebSocketClient({
      url: wsUrl,
      sessionId,
      apiKey: apiKey ?? "",
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
export { deriveAgentId, deriveLangGraphAgentId } from "./identity.js";
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
