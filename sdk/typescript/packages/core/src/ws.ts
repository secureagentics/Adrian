import WebSocket from "ws";
import type { AdrianCallbackHandler } from "./handler.js";
import type { PairedEvent } from "./format/types.js";
import type { EventHandler, McpServer } from "./types.js";
import { decodeServerFrame, encodeClientFrame, Mode, SCHEMA_VERSION, type PolicySnapshot, type Verdict } from "./proto/schema.js";

const INITIAL_BACKOFF_MS = 1000;
const MAX_BACKOFF_MS = 30_000;
const QUOTA_EXHAUSTED_CLOSE_CODE = 4003;
const QUOTA_RECONNECT_DELAY_MS = 60_000;
const MAX_RUN_ID_MAP = 1024;
const MAX_TOOL_CALL_MAP = 1024;
const MAX_VERDICT_CACHE = 1024;

type VerdictWaiter = { resolve: (verdict: Verdict | null) => void; timer?: ReturnType<typeof setTimeout> };
type LoginAckWaiter = { resolve: (acked: boolean) => void; timer?: ReturnType<typeof setTimeout> };

export class WebSocketClient implements EventHandler {
  private url: string;
  private sessionId: string;
  private apiKey: string;
  private onDisconnect?: ((reason: string) => void | Promise<void>) | null;
  private onReconnect?: (() => void | Promise<void>) | null;
  private onLoginAck?: (() => void | Promise<void>) | null;
  private ws: WebSocket | null = null;
  private loggedIn = false;
  private closing = false;
  private replaying = false;
  private hadConnection = false;
  private provider = "";
  private model = "";
  private mode = Mode.MODE_UNSPECIFIED;
  private policy: PolicySnapshot | null = null;
  private replayBuffer: Uint8Array[] = [];
  private replayLimit: number;
  private droppedFrames = 0;
  private runIdToEventId = new Map<string, string>();
  private toolCallIdToEventId = new Map<string, string>();
  private pendingVerdicts = new Map<string, VerdictWaiter[]>();
  private verdictCache = new Map<string, Verdict>();
  private loginAckWaiters = new Set<LoginAckWaiter>();
  handler: AdrianCallbackHandler | null;

  constructor(options: {
    url: string;
    sessionId: string;
    apiKey: string;
    handler?: AdrianCallbackHandler | null;
    onDisconnect?: ((reason: string) => void | Promise<void>) | null;
    onReconnect?: (() => void | Promise<void>) | null;
    onLoginAck?: (() => void | Promise<void>) | null;
    replayBufferFrames?: number;
  }) {
    this.url = options.url;
    this.sessionId = options.sessionId;
    this.apiKey = options.apiKey;
    this.handler = options.handler ?? null;
    this.onDisconnect = options.onDisconnect;
    this.onReconnect = options.onReconnect;
    this.onLoginAck = options.onLoginAck;
    this.replayLimit = options.replayBufferFrames ?? 1000;
  }

  scheduleConnect(): void {
    void this.connectLoop();
  }

  async onPairedEvent(event: PairedEvent): Promise<void> {
    if (event.data.kind === "llm") {
      if (!this.provider) this.provider = deriveProvider(event.data.model);
      if (!this.model) this.model = event.data.model;
      this.setLru(this.runIdToEventId, event.runId, event.eventId, MAX_RUN_ID_MAP);
      for (const call of event.data.toolCalls) {
        if (call.id) this.setLru(this.toolCallIdToEventId, call.id, event.eventId, MAX_TOOL_CALL_MAP);
      }
    }
    await this.sendFrame(encodeClientFrame({ pairedBatch: { events: [event] } }));
  }

  async sendMcpInventory(servers: McpServer[]): Promise<void> {
    if (servers.length === 0) return;
    await this.sendFrame(encodeClientFrame({ mcpInventory: { servers } }));
  }

  policyActive(): boolean {
    return this.mode === Mode.MODE_BLOCK || this.mode === Mode.MODE_HITL;
  }

  loginAcked(): boolean {
    return this.loggedIn;
  }

  async waitForPolicyReady(timeoutSeconds: number | null): Promise<boolean> {
    if (this.loggedIn) return true;
    if (this.closing) return false;
    return new Promise((resolve) => {
      const waiter: LoginAckWaiter = { resolve };
      if (timeoutSeconds !== null) {
        waiter.timer = setTimeout(() => {
          this.loginAckWaiters.delete(waiter);
          resolve(false);
        }, timeoutSeconds * 1000);
      }
      this.loginAckWaiters.add(waiter);
    });
  }

  blockTimeout(defaultSeconds: number): number | null {
    if (this.mode === Mode.MODE_HITL) return null;
    if (this.mode === Mode.MODE_BLOCK) return defaultSeconds;
    return 0;
  }

  async waitForToolCallVerdict(toolCallId: string, timeoutSeconds: number | null): Promise<Verdict | null> {
    const eventId = this.toolCallIdToEventId.get(toolCallId);
    if (!eventId) return null;
    return this.waitForVerdict(eventId, timeoutSeconds);
  }

  async waitForVerdict(eventId: string, timeoutSeconds: number | null): Promise<Verdict | null> {
    const cached = this.verdictCache.get(eventId);
    if (cached) return cached;
    return new Promise((resolve) => {
      const entry: VerdictWaiter = { resolve };
      if (timeoutSeconds !== null) {
        entry.timer = setTimeout(() => this.resolveVerdict(eventId, null), timeoutSeconds * 1000);
      }
      const waiters = this.pendingVerdicts.get(eventId) ?? [];
      waiters.push(entry);
      this.pendingVerdicts.set(eventId, waiters);
    });
  }

  async close(): Promise<void> {
    this.closing = true;
    this.ws?.close();
    for (const [eventId] of this.pendingVerdicts) this.resolveVerdict(eventId, null);
    this.resolveLoginAckWaiters(false);
  }

  private async connectLoop(): Promise<void> {
    let backoff = INITIAL_BACKOFF_MS;
    while (!this.closing) {
      try {
        await this.connectOnce();
        backoff = INITIAL_BACKOFF_MS;
        await this.waitForClose();
      } catch {
        // Connection errors are retried with backoff.
      }
      if (this.closing) return;
      const delay = this.wsCloseCode() === QUOTA_EXHAUSTED_CLOSE_CODE ? QUOTA_RECONNECT_DELAY_MS : backoff;
      await sleep(delay);
      backoff = Math.min(backoff * 2, MAX_BACKOFF_MS);
    }
  }

  private async connectOnce(): Promise<void> {
    await new Promise<void>((resolve, reject) => {
      const ws = new WebSocket(this.url, { headers: { Authorization: `Bearer ${this.apiKey}` } });
      this.ws = ws;
      ws.binaryType = "arraybuffer";
      ws.once("open", () => {
        void this.sendRaw(encodeClientFrame({ login: { sessionId: this.sessionId, llmStack: { provider: this.provider, model: this.model }, schemaVersion: SCHEMA_VERSION } }));
        resolve();
      });
      ws.on("message", (data) => void this.handleMessage(data));
      ws.once("error", reject);
      ws.once("close", () => {
        this.loggedIn = false;
        this.replaying = false;
        this.policy = null;
        this.mode = Mode.MODE_UNSPECIFIED;
        this.resolveLoginAckWaiters(false);
        if (!this.closing) void this.onDisconnect?.("recv_loop_exit");
      });
    });
  }

  private waitForClose(): Promise<void> {
    const ws = this.ws;
    if (!ws || ws.readyState === WebSocket.CLOSED) return Promise.resolve();
    return new Promise((resolve) => ws.once("close", () => resolve()));
  }

  private wsCloseCode(): number | null {
    return null;
  }

  private async handleMessage(data: WebSocket.RawData): Promise<void> {
    const bytes = data instanceof Buffer ? data : Buffer.from(data as ArrayBuffer);
    let frame: ReturnType<typeof decodeServerFrame>;
    try {
      frame = decodeServerFrame(bytes);
    } catch {
      return;
    }
    if ("loginAck" in frame) {
      this.policy = frame.loginAck.policy;
      this.mode = frame.loginAck.policy.mode;
      this.loggedIn = true;
      this.resolveLoginAckWaiters(true);
      if (this.hadConnection) await this.onReconnect?.();
      this.hadConnection = true;
      await this.drainReplayBuffer();
      await this.onLoginAck?.();
      return;
    }
    const verdict = frame.verdict;
    this.resolveVerdict(verdict.eventId, verdict);
    await this.handler?.handleVerdict(verdict);
  }

  private async sendFrame(frame: Uint8Array): Promise<void> {
    if (!this.loggedIn || this.replaying || !this.ws || this.ws.readyState !== WebSocket.OPEN) {
      this.bufferFrame(frame);
      return;
    }
    try {
      await this.sendRaw(frame);
    } catch {
      this.bufferFrame(frame);
      await this.onDisconnect?.("send_failure");
    }
  }

  private async sendRaw(frame: Uint8Array): Promise<void> {
    const ws = this.ws;
    if (!ws || ws.readyState !== WebSocket.OPEN) throw new Error("websocket is not open");
    await new Promise<void>((resolve, reject) => ws.send(frame, { binary: true }, (err) => err ? reject(err) : resolve()));
  }

  private async drainReplayBuffer(): Promise<void> {
    this.replaying = true;
    try {
      while (this.replayBuffer.length > 0 && this.ws?.readyState === WebSocket.OPEN) {
        const frame = this.replayBuffer.shift();
        if (frame) await this.sendRaw(frame);
      }
    } finally {
      this.replaying = false;
    }
    this.droppedFrames = 0;
  }

  private bufferFrame(frame: Uint8Array): void {
    if (this.replayLimit <= 0) return;
    if (this.replayBuffer.length >= this.replayLimit) {
      this.replayBuffer.shift();
      this.droppedFrames += 1;
    }
    this.replayBuffer.push(frame);
  }

  private resolveVerdict(eventId: string, verdict: Verdict | null): void {
    if (verdict) this.setLru(this.verdictCache, eventId, verdict, MAX_VERDICT_CACHE);
    const waiters = this.pendingVerdicts.get(eventId);
    if (!waiters) return;
    this.pendingVerdicts.delete(eventId);
    for (const entry of waiters) {
      if (entry.timer) clearTimeout(entry.timer);
      entry.resolve(verdict);
    }
  }

  private resolveLoginAckWaiters(acked: boolean): void {
    for (const waiter of this.loginAckWaiters) {
      if (waiter.timer) clearTimeout(waiter.timer);
      waiter.resolve(acked);
    }
    this.loginAckWaiters.clear();
  }

  private setLru<V>(map: Map<string, V>, key: string, value: V, limit: number): void {
    if (map.has(key)) map.delete(key);
    map.set(key, value);
    while (map.size > limit) map.delete(map.keys().next().value as string);
  }
}

export function shouldHalt(verdict: Verdict): boolean {
  if (verdict.hitl) return !verdict.hitl.continueExecution;
  const prefix = verdict.madCode.slice(0, 2);
  if (prefix === "M0") return verdict.policy.policyM0;
  if (prefix === "M2") return verdict.policy.policyM2;
  if (prefix === "M3") return verdict.policy.policyM3;
  if (prefix === "M4") return verdict.policy.policyM4;
  return false;
}

function deriveProvider(modelClassName: string): string {
  const key = modelClassName.toLowerCase();
  return ({ chatanthropic: "anthropic", chatopenai: "openai", chatgooglegenai: "google", chatcohere: "cohere", chatmistralai: "mistral" } as Record<string, string>)[key] ?? key;
}

function sleep(ms: number): Promise<void> {
  return new Promise((resolve) => setTimeout(resolve, ms));
}
