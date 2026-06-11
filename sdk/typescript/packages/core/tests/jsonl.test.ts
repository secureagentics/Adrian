import { mkdtemp, readFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { describe, expect, it } from "vitest";
import { JSONLHandler } from "../src/handlers/jsonl.js";
import type { PairedEvent } from "../src/format/types.js";

it("writes paired events as jsonl", async () => {
  const dir = await mkdtemp(join(tmpdir(), "adrian-ts-"));
  const path = join(dir, "events.jsonl");
  const handler = new JSONLHandler(path);
  const event: PairedEvent = {
    eventId: "evt",
    invocationId: "inv",
    sessionId: "sess",
    runId: "run",
    parentRunId: "",
    timestamp: new Date(0).toISOString(),
    pairType: "tool",
    agent: { agentId: "agent", systemPrompt: "", userInstruction: "" },
    parent: null,
    data: { kind: "tool", toolName: "search", toolCallId: null, input: "x", output: "y" },
    metadata: null,
  };
  await handler.onPairedEvent(event);
  await handler.close();
  expect(await readFile(path, "utf8")).toContain('"eventId":"evt"');
});
