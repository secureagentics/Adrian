// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { readFileSync } from "node:fs";
import protobuf from "protobufjs";
import { describe, expect, it } from "vitest";
import { protoSource } from "../src/proto/schema.js";

// Python and Go bindings are generated from proto/event.proto; the TS SDK
// instead carries a hand-written copy of the schema, so nothing stops the two
// drifting apart. A wrong field number would corrupt decoding silently, and a
// missing field means TS can neither send nor read it. These tests parse both
// and compare, so any change to the canonical schema fails here until the
// mirror is updated to match.

const CANONICAL_PATH = new URL("../../../../../proto/event.proto", import.meta.url);

function load(source: string): protobuf.Root {
  return protobuf.parse(source, { keepCase: true }).root;
}

/** protobufjs reports fullName dot-prefixed; drop it so keys read naturally. */
function qualified(name: string): string {
  return name.replace(/^\./, "");
}

/** Fully-qualified name -> {field: number} for every message in a root. */
function messageFields(root: protobuf.Root): Map<string, Map<string, number>> {
  const out = new Map<string, Map<string, number>>();
  const walk = (ns: protobuf.NamespaceBase) => {
    for (const nested of ns.nestedArray) {
      if (nested instanceof protobuf.Type) {
        const fields = new Map<string, number>();
        for (const field of nested.fieldsArray) fields.set(field.name, field.id);
        out.set(qualified(nested.fullName), fields);
      }
      if (nested instanceof protobuf.Namespace) walk(nested);
    }
  };
  walk(root);
  return out;
}

/** Fully-qualified name -> {value: number} for every enum in a root. */
function enumValues(root: protobuf.Root): Map<string, Record<string, number>> {
  const out = new Map<string, Record<string, number>>();
  const walk = (ns: protobuf.NamespaceBase) => {
    for (const nested of ns.nestedArray) {
      if (nested instanceof protobuf.Enum) out.set(qualified(nested.fullName), nested.values);
      if (nested instanceof protobuf.Namespace) walk(nested);
    }
  };
  walk(root);
  return out;
}

/** Field name -> declared type, for comparing types as well as numbers. */
function fieldTypes(root: protobuf.Root, fullName: string): Map<string, string> {
  const type = root.lookupType(fullName);
  const out = new Map<string, string>();
  for (const field of type.fieldsArray) {
    out.set(field.name, `${field.repeated ? "repeated " : ""}${field.type}`);
  }
  return out;
}

describe("TS proto mirror matches proto/event.proto", () => {
  const canonical = load(readFileSync(CANONICAL_PATH, "utf8"));
  const mirror = load(protoSource);
  const canonicalMessages = messageFields(canonical);
  const mirrorMessages = messageFields(mirror);

  it("parses the canonical schema", () => {
    // Guards the test itself: a silently-empty parse would pass everything.
    expect(canonicalMessages.size).toBeGreaterThan(10);
    expect(canonicalMessages.get("adrian.core_api.v1.TokenUsage")).toEqual(
      new Map([["prompt_tokens", 1], ["completion_tokens", 2], ["total_tokens", 3]]),
    );
  });

  it("declares every message the canonical schema defines", () => {
    const missing = [...canonicalMessages.keys()].filter((name) => !mirrorMessages.has(name));
    expect(missing).toEqual([]);
  });

  it("declares every canonical field, with matching numbers", () => {
    const problems: string[] = [];
    for (const [name, canonicalFields] of canonicalMessages) {
      const mirrored = mirrorMessages.get(name);
      if (!mirrored) continue; // covered by the message-level test above
      for (const [field, number] of canonicalFields) {
        if (!mirrored.has(field)) {
          problems.push(`${name}.${field}=${number} missing from the mirror`);
        } else if (mirrored.get(field) !== number) {
          problems.push(`${name}.${field}: mirror=${mirrored.get(field)} canonical=${number}`);
        }
      }
    }
    expect(problems).toEqual([]);
  });

  it("invents no fields the canonical schema does not define", () => {
    const extra: string[] = [];
    for (const [name, mirrored] of mirrorMessages) {
      const canonicalFields = canonicalMessages.get(name);
      if (!canonicalFields) {
        extra.push(`${name} is not in the canonical schema`);
        continue;
      }
      for (const field of mirrored.keys()) {
        if (!canonicalFields.has(field)) extra.push(`${name}.${field}`);
      }
    }
    expect(extra).toEqual([]);
  });

  it("matches canonical field types", () => {
    const problems: string[] = [];
    for (const name of mirrorMessages.keys()) {
      if (!canonicalMessages.has(name)) continue;
      const canonicalTypes = fieldTypes(canonical, name);
      const mirrorTypes = fieldTypes(mirror, name);
      for (const [field, type] of canonicalTypes) {
        const mirrored = mirrorTypes.get(field);
        if (mirrored !== undefined && mirrored !== type) {
          problems.push(`${name}.${field}: mirror=${mirrored} canonical=${type}`);
        }
      }
    }
    expect(problems).toEqual([]);
  });

  it("matches canonical enum values", () => {
    const canonicalEnums = enumValues(canonical);
    const mirrorEnums = enumValues(mirror);
    const problems: string[] = [];
    for (const [name, values] of canonicalEnums) {
      const mirrored = mirrorEnums.get(name);
      if (!mirrored) {
        problems.push(`${name} missing from the mirror`);
        continue;
      }
      for (const [key, number] of Object.entries(values)) {
        if (mirrored[key] !== number) {
          problems.push(`${name}.${key}: mirror=${mirrored[key]} canonical=${number}`);
        }
      }
    }
    expect(problems).toEqual([]);
  });

  it("keeps reasoning on field 6 of LlmPairData", () => {
    // The field this branch added; pinned so a renumber cannot slip through.
    expect(mirrorMessages.get("adrian.core_api.v1.LlmPairData")?.get("reasoning")).toBe(6);
    expect(canonicalMessages.get("adrian.core_api.v1.LlmPairData")?.get("reasoning")).toBe(6);
  });
});
