import { currentConfig, isInitialized } from "./config.js";
import type { McpServer } from "./types.js";

const servers = new Map<string, McpServer>();

export function mcpServers(): McpServer[] {
  return [...servers.values()];
}

export function resetMcpServers(): void {
  servers.clear();
}

export function registerMcpServer(server: McpServer): void {
  const previous = servers.get(server.name);
  if (previous?.transport === server.transport && previous.endpoint === server.endpoint) return;
  servers.set(server.name, server);
  if (isInitialized()) void currentConfig()?.onMcpServer?.(server);
}

export function registerMcpConnection(name: string, connection: unknown): void {
  if (!name) return;
  registerMcpServer(serverFromConnection(name, connection));
}

export async function patchMcpAdapters(): Promise<void> {
  await patchMcpTransports();
}

function serverFromConnection(name: string, connection: unknown): McpServer {
  if (!connection || typeof connection !== "object") return { name, transport: "unknown", endpoint: "" };
  const conn = connection as Record<string, unknown>;
  const transport = String(conn.transport ?? "unknown").toLowerCase();
  return { name, transport, endpoint: endpointFor(transport, conn) };
}

function endpointFor(transport: string, conn: Record<string, unknown>): string {
  if (transport === "stdio") {
    const command = String(conn.command ?? "");
    const args = Array.isArray(conn.args) ? conn.args.map(String) : [];
    return [command, ...args].filter(Boolean).join(" ");
  }
  if (["sse", "websocket", "streamable_http", "streamable-http", "http"].includes(transport)) return String(conn.url ?? "");
  return "";
}

async function patchMcpTransports(): Promise<void> {
  const targets: Array<[string, string, string]> = [
    ["@modelcontextprotocol/sdk/client/stdio.js", "stdio_client", "stdio"],
    ["@modelcontextprotocol/sdk/client/sse.js", "sse_client", "sse"],
    ["@modelcontextprotocol/sdk/client/websocket.js", "websocket_client", "websocket"],
  ];
  for (const [specifier, attr, transport] of targets) {
    const mod = await importOptional(specifier);
    const original = mod?.[attr];
    if (!original || original._adrianMcpPatched) continue;
    mod[attr] = function patchedTransport(...args: unknown[]) {
      registerSynthesised(transport, endpointFromTransportArgs(transport, args));
      return original(...args);
    };
    mod[attr]._adrianMcpPatched = true;
  }
}

function registerSynthesised(transport: string, endpoint: string): void {
  if (!endpoint && transport === "unknown") return;
  if ([...servers.values()].some((server) => server.transport === transport && server.endpoint === endpoint)) return;
  registerMcpServer({ name: endpoint ? `${transport}:${endpoint}` : transport, transport, endpoint });
}

function endpointFromTransportArgs(transport: string, args: unknown[]): string {
  const first = args[0];
  if (transport === "stdio" && first && typeof first === "object") {
    const params = first as Record<string, unknown>;
    return [String(params.command ?? ""), ...(Array.isArray(params.args) ? params.args.map(String) : [])].filter(Boolean).join(" ");
  }
  if (typeof first === "string") return first;
  if (first instanceof URL) return first.toString();
  if (first && typeof first === "object" && "url" in first) return String((first as Record<string, unknown>).url ?? "");
  return "";
}

async function importOptional(specifier: string): Promise<any | null> {
  try { return await import(specifier); } catch { return null; }
}
