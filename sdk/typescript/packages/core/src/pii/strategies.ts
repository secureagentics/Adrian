import { createHash } from "node:crypto";
import { Detection, PiiType } from "./patterns.js";

export enum RedactionStrategy {
  REPLACE = "replace",
  MASK = "mask",
  HASH = "hash",
}

export function applyStrategy(detection: Detection, strategy: RedactionStrategy): string {
  if (strategy === RedactionStrategy.HASH) {
    const digest = createHash("sha256").update(detection.text).digest("hex").slice(0, 8);
    return `[${detection.piiType}:${digest}]`;
  }
  if (strategy === RedactionStrategy.MASK) return mask(detection);
  return `[${detection.piiType}_REDACTED]`;
}

function mask(detection: Detection): string {
  const text = detection.text;
  switch (detection.piiType) {
    case PiiType.EMAIL: {
      return maskEmail(text);
    }
    case PiiType.PHONE:
      return maskPhone(text);
    case PiiType.SSN:
      return maskSsn(text);
    case PiiType.CREDIT_CARD:
      return maskCreditCard(text);
    case PiiType.IP_ADDRESS:
      return maskIp(text);
    default:
      return text.length <= 2 ? "*".repeat(text.length) : text[0] + "*".repeat(text.length - 2) + text.at(-1);
  }
}

function maskEmail(text: string): string {
  const [local, domain] = text.split("@");
  const suffix = domain?.split(".").at(-1) ?? "";
  return `${local?.[0] ?? "*"}***@***.${suffix}`;
}

function maskPhone(text: string): string {
  const digits = text.replace(/\D/g, "");
  if (digits.length < 4) return "***";
  return `***-***-${digits.slice(-4)}`;
}

function maskSsn(text: string): string {
  const digits = text.replace(/\D/g, "");
  if (digits.length < 4) return "***-**-****";
  return `***-**-${digits.slice(-4)}`;
}

function maskCreditCard(text: string): string {
  const digits = text.replace(/\D/g, "");
  if (digits.length < 4) return "****-****-****-****";
  return `****-****-****-${digits.slice(-4)}`;
}

function maskIp(text: string): string {
  if (text.includes(":")) {
    const parts = text.split(":");
    const masked = Array(Math.max(0, parts.length - 1)).fill("****").concat(parts.slice(-1));
    return masked.join(":");
  }
  const last = text.split(".").slice(-1)[0] ?? "";
  return `***.***.***.${last}`;
}
