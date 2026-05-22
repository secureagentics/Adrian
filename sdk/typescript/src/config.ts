import type { EventData, McpServer, VerdictContext } from "./types.js";

export type MaybePromise<T> = T | Promise<T>;
export type OnVerdictCallback = (ctx: VerdictContext) => MaybePromise<void>;
export type OnBlockCallback = (ctx: VerdictContext) => MaybePromise<void>;
export type OnAuditCallback = (ctx: VerdictContext) => MaybePromise<void>;
export type OnEventCallback = (
  eventType: string,
  data: EventData,
  runId: string,
  parentRunId: string | null,
  eventId: string,
) => MaybePromise<void>;
export type OnDisconnectCallback = (reason: string) => MaybePromise<void>;
export type OnReconnectCallback = () => MaybePromise<void>;
export type OnMcpServerCallback = (server: McpServer) => MaybePromise<void>;

export interface AdrianConfig {
  apiKey: string | null;
  logFile: string;
  logLevel: string | null;
  sessionId: string;
  wsUrl: string | null;
  blockTimeout: number;
  onEvent: OnEventCallback | null;
  onVerdict: OnVerdictCallback | null;
  onBlock: OnBlockCallback | null;
  onAudit: OnAuditCallback | null;
  onDisconnect: OnDisconnectCallback | null;
  onReconnect: OnReconnectCallback | null;
  onMcpServer: OnMcpServerCallback | null;
  replayBufferFrames: number;
}

export interface InitOptions {
  apiKey?: string | null;
  logFile?: string;
  handlers?: import("./types.js").EventHandler[] | null;
  autoInstrument?: boolean;
  logLevel?: string | null;
  wsUrl?: string | null;
  sessionId?: string | null;
  blockTimeout?: number;
  onEvent?: OnEventCallback | null;
  onVerdict?: OnVerdictCallback | null;
  onBlock?: OnBlockCallback | null;
  onAudit?: OnAuditCallback | null;
  onDisconnect?: OnDisconnectCallback | null;
  onReconnect?: OnReconnectCallback | null;
  onMcpServer?: OnMcpServerCallback | null;
  replayBufferFrames?: number;
}

let config: AdrianConfig | null = null;

export function getConfig(): AdrianConfig {
  if (config === null) {
    throw new Error("Adrian SDK has not been initialised. Call init() first.");
  }
  return config;
}

export function currentConfig(): AdrianConfig | null {
  return config;
}

export function setConfig(next: AdrianConfig | null): void {
  config = next;
}

export function isInitialized(): boolean {
  return config !== null;
}
