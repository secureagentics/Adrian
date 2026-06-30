import { describe, expect, it } from "vitest";
import { EventPairBuffer } from "../src/pairing.js";

it("pairs chat model start and llm end events", () => {
  const buffer = new EventPairBuffer();
  buffer.onStart({
    eventType: "chat_model_start",
    data: { model: "ChatOpenAI", messages: [{ role: "system", content: "sys" }, { role: "human", content: "hi" }], metadata: null },
    runId: "run-1",
    agentId: "agent",
    parent: null,
    metadata: null,
  });
  const pair = buffer.onEnd({ eventType: "llm_end", data: { output: "hello", toolCalls: [], usage: null }, runId: "run-1", invocationId: "inv", sessionId: "sess" });
  expect(pair?.pairType).toBe("llm");
  expect(pair?.agent.systemPrompt).toBe("sys");
  expect(pair?.agent.userInstruction).toBe("hi");
});
