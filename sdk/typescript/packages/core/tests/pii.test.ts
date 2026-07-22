// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { describe, expect, it } from "vitest";
import { redactText, PiiType, RedactionStrategy } from "../src/pii/index.js";
import { applyStrategy } from "../src/pii/strategies.js";

describe("PII patterns parity with Python", () => {
  it("preserves passport label and redacts only digits", () => {
    const r = redactText("passport: 123456789");
    expect(r.text).toContain("passport");
    expect(r.text).toContain("[PASSPORT_REDACTED]");
  });

  it("recognizes DL and D.L. forms and redacts only the id", () => {
    const r1 = redactText("DL: A12345");
    expect(r1.text).toContain("DL");
    expect(r1.text).toContain("[DRIVER_LICENSE_REDACTED]");

    const r2 = redactText("D.L.: Z99999");
    expect(r2.text).toContain("D.L.");
    expect(r2.text).toContain("[DRIVER_LICENSE_REDACTED]");
  });

  it("redacts private IPv6 but not public IPv6", () => {
    const privateR = redactText("host fe80::1", { strategy: RedactionStrategy.REPLACE });
    expect(privateR.text).toContain("[IP_ADDRESS_REDACTED]");

    const publicR = redactText("dns 2001:4860:4860::8888", { strategy: RedactionStrategy.REPLACE });
    expect(publicR.text).not.toContain("[IP_ADDRESS_REDACTED]");
  });

  it("masks IPv6 preserving only last group", () => {
    const masked = redactText("ip fe80::1", { strategy: RedactionStrategy.MASK });
    expect(masked.text).toContain(":1");
  });

  it("requires postal context for US ZIP but not arbitrary 5-digit numbers", () => {
    const zip = redactText("My zip is 02115");
    expect(zip.text).toContain("[POSTAL_CODE_REDACTED]");

    const order = redactText("Order 12345");
    expect(order.text).not.toContain("[POSTAL_CODE_REDACTED]");
  });

  it("requires DOB context for dates", () => {
    const withContext = redactText("born 1 January 1970");
    expect(withContext.text).toContain("[DATE_OF_BIRTH_REDACTED]");

    const without = redactText("1 January 1970");
    expect(without.text).not.toContain("[DATE_OF_BIRTH_REDACTED]");
  });

  it("detects street addresses with secondary unit (Apt/Suite)", () => {
    const r = redactText("123 Main Street Apt 4");
    expect(r.text).toContain("[STREET_ADDRESS_REDACTED]");
  });

  it("driver license requires separator like [:.#]", () => {
    const withSep = redactText("driver license: 12345");
    expect(withSep.text).toContain("[DRIVER_LICENSE_REDACTED]");

    const withoutSep = redactText("driver license 12345");
    expect(withoutSep.text).not.toContain("[DRIVER_LICENSE_REDACTED]");
  });

  it("masking functions return all-asterisks when fewer than 4 digits", () => {
    const phoneDet = { piiType: PiiType.PHONE, start: 0, end: 3, text: "123" };
    const phoneMasked = applyStrategy(phoneDet as any, RedactionStrategy.MASK);
    expect(phoneMasked).toBe("***");

    const ssnDet = { piiType: PiiType.SSN, start: 0, end: 3, text: "123" };
    const ssnMasked = applyStrategy(ssnDet as any, RedactionStrategy.MASK);
    expect(ssnMasked).toBe("***-**-****");

    const ccDet = { piiType: PiiType.CREDIT_CARD, start: 0, end: 3, text: "123" };
    const ccMasked = applyStrategy(ccDet as any, RedactionStrategy.MASK);
    expect(ccMasked).toBe("****-****-****-****");
  });
});
