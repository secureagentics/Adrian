import { describe, expect, it } from "vitest";
import { cwdKey } from "../src/sessionPersistence.js";

it("encodes cwd into a flat key", () => {
  expect(cwdKey("/tmp/adrian")).toContain("-tmp-adrian");
});
