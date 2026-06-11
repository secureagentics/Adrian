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

function envString(name: string): string | undefined {
  const value = process.env[name];
  return value !== undefined && value !== "" ? value : undefined;
}

export function resolveInitOptions(options: InitOptions): {
  apiKey: string | null;
  logFile: string;
  wsUrl: string | null;
  blockTimeout: number;
  replayBufferFrames: number;
} {
  const apiKey = options.apiKey !== undefined
    ? options.apiKey
    : (envString("ADRIAN_API_KEY") ?? null);

  const logFile = options.logFile !== undefined
    ? options.logFile
    : (envString("ADRIAN_LOG_FILE") ?? "events.jsonl");

  const wsUrl = options.wsUrl !== undefined
    ? options.wsUrl
    : (envString("ADRIAN_WS_URL") ?? "ws://localhost:8080/ws");

  const blockTimeout = options.blockTimeout !== undefined
    ? options.blockTimeout
    : Number(envString("ADRIAN_BLOCK_TIMEOUT") ?? 30);

  let replayBufferFrames = options.replayBufferFrames ?? 1000;
  const envReplay = envString("ADRIAN_REPLAY_BUFFER_FRAMES");
  if (options.replayBufferFrames === undefined && envReplay !== undefined) {
    const parsed = parseInt(envReplay, 10);
    if (Number.isFinite(parsed)) replayBufferFrames = parsed;
  }

  return {
    apiKey,
    logFile,
    wsUrl,
    blockTimeout: Number.isFinite(blockTimeout) ? blockTimeout : 30,
    replayBufferFrames: Number.isFinite(replayBufferFrames) ? replayBufferFrames : 1000,
  };
}
