import { randomUUID } from "node:crypto";
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { homedir } from "node:os";
import { resolve, sep } from "node:path";

const CONFIG_FILENAME = "config.json";
const SESSION_KEY = "session_id";

export function cwdKey(cwd = process.cwd()): string {
  return resolve(cwd).replaceAll("/", "-").replaceAll("\\", "-").replaceAll(":", "-");
}

export function configDir(cwd = process.cwd()): string {
  return [homedir(), ".adrian", "projects", cwdKey(cwd)].join(sep);
}

export function configPath(cwd = process.cwd()): string {
  return [configDir(cwd), CONFIG_FILENAME].join(sep);
}

export async function resolveSessionId(cwd = process.cwd()): Promise<string> {
  const existing = await readPersisted(cwd);
  if (existing) return existing;
  const next = randomUUID();
  await writePersisted(next, cwd);
  return next;
}

export async function envAwareResolveSessionId(explicit?: string | null, cwd = process.cwd()): Promise<string> {
  if (explicit !== undefined && explicit !== null) return explicit;
  if (process.env.ADRIAN_SESSION_ID) return process.env.ADRIAN_SESSION_ID;
  return resolveSessionId(cwd);
}

async function readPersisted(cwd: string): Promise<string | null> {
  try {
    const raw = await readFile(configPath(cwd), "utf8");
    const data = JSON.parse(raw) as Record<string, unknown>;
    const sessionId = data[SESSION_KEY];
    return typeof sessionId === "string" && sessionId.length > 0 ? sessionId : null;
  } catch {
    return null;
  }
}

async function writePersisted(sessionId: string, cwd: string): Promise<void> {
  try {
    const path = configPath(cwd);
    await mkdir(configDir(cwd), { recursive: true });
    await writeFile(path, JSON.stringify({ [SESSION_KEY]: sessionId }, null, 2) + "\n", "utf8");
  } catch {
    // Persistence is best effort; init can still proceed with the generated id.
  }
}
