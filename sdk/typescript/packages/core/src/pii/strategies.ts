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
      const [local, domain] = text.split("@");
      const suffix = domain?.split(".").at(-1) ?? "";
      return `${local?.[0] ?? "*"}***@***.${suffix}`;
    }
    case PiiType.PHONE:
      return `***-***-${text.replace(/\D/g, "").slice(-4)}`;
    case PiiType.SSN:
      return `***-**-${text.replace(/\D/g, "").slice(-4)}`;
    case PiiType.CREDIT_CARD:
      return `****-****-****-${text.replace(/\D/g, "").slice(-4)}`;
    case PiiType.IP_ADDRESS:
      return text.includes(":") ? `****:${text.split(":").at(-1) ?? ""}` : `***.***.***.${text.split(".").at(-1) ?? ""}`;
    default:
      return text.length <= 2 ? "*".repeat(text.length) : text[0] + "*".repeat(text.length - 2) + text.at(-1);
  }
}
