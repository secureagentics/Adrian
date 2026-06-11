import type { AdrianCallbackHandler } from "./handler.js";
import type { WebSocketClient } from "./ws.js";

let handler: AdrianCallbackHandler | null = null;
let wsClient: WebSocketClient | null = null;

export function getHandler(): AdrianCallbackHandler | null {
  return handler;
}

export function getWebSocketClient(): WebSocketClient | null {
  return wsClient;
}

export function setRuntime(nextHandler: AdrianCallbackHandler | null, nextWsClient: WebSocketClient | null): void {
  handler = nextHandler;
  wsClient = nextWsClient;
}
