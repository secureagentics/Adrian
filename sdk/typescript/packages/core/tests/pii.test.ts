import { describe, expect, it } from "vitest";
import { redactText, RedactionStrategy } from "../src/pii/index.js";

it("redacts email addresses", () => {
  const result = redactText("email me at test@example.com");
  expect(result.text).toContain("[EMAIL_REDACTED]");
});

it("can mask phone numbers", () => {
  const result = redactText("call 415-555-1234", { strategy: RedactionStrategy.MASK });
  expect(result.text).toContain("***-***-1234");
});
