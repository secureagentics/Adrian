import { describe, expect, it } from "vitest";
import { encodeClientFrame, SCHEMA_VERSION } from "../src/proto/schema.js";
import type { PairedEvent } from "../src/format/types.js";

it("encodes login and paired event frames", () => {
  const login = encodeClientFrame({ login: { sessionId: "sess", llmStack: { provider: "openai", model: "gpt" }, schemaVersion: SCHEMA_VERSION } });
  expect(login.length).toBeGreaterThan(0);

  const event: PairedEvent = {
    eventId: "evt",
    invocationId: "inv",
    sessionId: "sess",
    runId: "run",
    parentRunId: "",
    timestamp: new Date(0).toISOString(),
    pairType: "llm",
    agent: { agentId: "agent", systemPrompt: "", userInstruction: "" },
    parent: null,
    data: { kind: "llm", model: "ChatOpenAI", messages: [], output: "ok", toolCalls: [], usage: null },
    metadata: null,
  };
  const batch = encodeClientFrame({ pairedBatch: { events: [event] } });
  expect(batch.length).toBeGreaterThan(0);
});
