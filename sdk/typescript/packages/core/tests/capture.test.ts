import { describe, expect, it } from "vitest";
import { messagesFromPromptLike, normalizeResponseInput } from "../src/capture/common.js";

describe("normalizeResponseInput", () => {
  it("maps role-based input items", () => {
    expect(normalizeResponseInput([
      { role: "user", content: "hello" },
    ])).toEqual([{ role: "user", content: "hello" }]);
  });

  it("maps function calls and outputs", () => {
    expect(normalizeResponseInput([
      { type: "function_call", name: "get_weather", arguments: '{"city":"SF"}' },
      { type: "function_call_output", output: '{"temp":58}' },
    ])).toEqual([
      { role: "assistant", content: '[tool_call:get_weather] {"city":"SF"}' },
      { role: "tool", content: '{"temp":58}' },
    ]);
  });
});

describe("messagesFromPromptLike", () => {
  it("prepends instructions as system for string input", () => {
    expect(messagesFromPromptLike({
      instructions: "You are helpful.",
      input: "Run the task.",
    })).toEqual([
      { role: "system", content: "You are helpful." },
      { role: "user", content: "Run the task." },
    ]);
  });

  it("prepends instructions for Responses API input arrays", () => {
    expect(messagesFromPromptLike({
      instructions: "You are an autonomous assistant.",
      input: [
        { role: "user", content: "Do the work." },
        { type: "function_call", name: "add_numbers", arguments: '{"a":1,"b":2}' },
        { type: "function_call_output", output: '{"result":3}' },
      ],
    })).toEqual([
      { role: "system", content: "You are an autonomous assistant." },
      { role: "user", content: "Do the work." },
      { role: "assistant", content: '[tool_call:add_numbers] {"a":1,"b":2}' },
      { role: "tool", content: '{"result":3}' },
    ]);
  });
});
