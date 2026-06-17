import { isIP } from "node:net";

export enum PiiType {
  EMAIL = "EMAIL",
  PHONE = "PHONE",
  SSN = "SSN",
  CREDIT_CARD = "CREDIT_CARD",
  IP_ADDRESS = "IP_ADDRESS",
  DATE_OF_BIRTH = "DATE_OF_BIRTH",
  IBAN = "IBAN",
  PASSPORT = "PASSPORT",
  STREET_ADDRESS = "STREET_ADDRESS",
  POSTAL_CODE = "POSTAL_CODE",
  DRIVER_LICENSE = "DRIVER_LICENSE",
  AWS_KEY = "AWS_KEY",
}

export interface Detection {
  piiType: PiiType;
  start: number;
  end: number;
  text: string;
}

type PatternEntry = [PiiType, RegExp, boolean, string | null];

// Named regexes (mirror Python naming)
const EMAIL_RE = /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g;
const PHONE_RE = /(?<![.\d])(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)/g;
const SSN_RE = /(?<!\d)(?!000|666|9\d\d)\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}(?!\d)/g;
const CREDIT_CARD_RE = /(?<!\d)(?:4\d{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}|5[1-5]\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}|3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}|6(?:011|5\d{2})[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})(?!\d)/g;
const IPV4_RE = /(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)/g;
const IPV6_RE = /(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}|(?:[0-9a-fA-F]{1,4}:){1,7}:|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}|::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}|::1/g;
const DATE_RE = /\b(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4})\b/gi;
const IBAN_RE = /\b[A-Z]{2}\d{2}\s?(?:[A-Z0-9]{4}\s?){2,7}[A-Z0-9]{1,4}\b/gi;
const PASSPORT_RE = /\bpassport[\s_-]*(?:no|number|#|num)?\.?\s*[:.#]\s*(\d{9})\b/gi;
const POSTAL_CONTEXT_RE = /\b(?:zip(?:\s*code)?|postal(?:\s*code)?)\b/i;
const STREET_ADDRESS_RE = /\b\d{1,5}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-zA-Z]+\s+){1,3}(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Road|Rd|Court|Ct|Place|Pl|Way|Circle|Cir|Parkway|Pkwy|Highway|Hwy)\.?(?:\s+(?:Apt|Suite|Ste|Unit|#)\s*\.?\s*\d+[A-Za-z]?)?/gi;
const US_ZIP_RE = /(?<!\d)\d{5}(?:-\d{4})?(?!\d)/g;
const UK_POSTCODE_RE = /\b(?:[A-PR-UWYZ][A-HK-Y0-9][AEHMNPRTVXY0-9]?[ABEHMNPRVWXY0-9]?\s*[0-9][ABD-HJLN-UW-Z]{2}|GIR\s*0AA)\b/gi;
const DRIVER_LICENSE_RE = /(?:driver'?s?\s+licen[cs]e|\bDL|\bD\.L\.)\s*(?:no|number|#|num)?\.?\s*[:.#]\s*([A-Z0-9]{5,15})\b/gi;
const AWS_KEY_RE = /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g;

const PATTERNS: PatternEntry[] = [
  [PiiType.EMAIL, EMAIL_RE, false, null],
  [PiiType.PHONE, PHONE_RE, false, null],
  [PiiType.SSN, SSN_RE, false, null],
  [PiiType.CREDIT_CARD, CREDIT_CARD_RE, false, "luhn"],
  [PiiType.IP_ADDRESS, IPV4_RE, false, "private_ip"],
  [PiiType.IP_ADDRESS, IPV6_RE, false, "private_ip"],
  [PiiType.DATE_OF_BIRTH, DATE_RE, false, "dob_context"],
  [PiiType.IBAN, IBAN_RE, false, null],
  [PiiType.PASSPORT, PASSPORT_RE, true, null],
  [PiiType.STREET_ADDRESS, STREET_ADDRESS_RE, false, null],
  [PiiType.POSTAL_CODE, US_ZIP_RE, false, "postal_context"],
  [PiiType.POSTAL_CODE, UK_POSTCODE_RE, false, null],
  [PiiType.DRIVER_LICENSE, DRIVER_LICENSE_RE, true, null],
  [PiiType.AWS_KEY, AWS_KEY_RE, false, null],
];

export function detect(text: string, types?: ReadonlySet<PiiType> | null): Detection[] {
  const detections: Detection[] = [];
  for (const [piiType, pattern, needsGroup, postFilter] of PATTERNS) {
    if (types && !types.has(piiType)) continue;
    pattern.lastIndex = 0;
    for (const match of text.matchAll(pattern)) {
      // Determine matched text and indices; support patterns that capture group(1)
      let matched: string;
      let start = match.index ?? 0;
      if (needsGroup && match[1] !== undefined) {
        matched = match[1];
        const whole = match[0];
        const groupIndex = whole.indexOf(matched);
        start = (match.index ?? 0) + (groupIndex >= 0 ? groupIndex : 0);
      } else {
        matched = match[0];
        start = match.index ?? 0;
      }

      const end = start + matched.length;

      // Post-filter validations
      switch (postFilter) {
        case "luhn": {
          if (!luhnCheck(matched.replace(/\D/g, ""))) continue;
          break;
        }
        case "private_ip": {
          if (!isPrivateIp(matched)) continue;
          break;
        }
        case "dob_context": {
          if (!hasDobContext(text, start, end)) continue;
          break;
        }
        case "postal_context": {
          if (!hasPostalContext(text, start, end)) continue;
          break;
        }
        default:
          break;
      }

      detections.push({ piiType, start, end, text: matched });
    }
  }
  // Sort detections by start (asc) and length (desc) then resolve overlaps
  detections.sort((a, b) => a.start - b.start || b.end - a.end);
  return resolveOverlaps(detections);
}

function luhnCheck(digits: string): boolean {
  let total = 0;
  const reverse = [...digits].reverse();
  for (let i = 0; i < reverse.length; i += 1) {
    let n = Number(reverse[i]);
    if (i % 2 === 1) {
      n *= 2;
      if (n > 9) n -= 9;
    }
    total += n;
  }
  return total % 10 === 0;
}

function resolveOverlaps(detections: Detection[]): Detection[] {
  if (detections.length === 0) return [];

  // Already sorted by start asc, longer matches first for equal starts.
  const result: Detection[] = [];
  let lastEnd = -1;

  for (const det of detections) {
    if (det.start >= lastEnd) {
      result.push(det);
      lastEnd = det.end;
    }
  }

  return result;
}

function isPrivateIp(input: string): boolean {
  const text = input.split("%")[0];
  const v = isIP(text);
  if (v === 4) {
    const [a, b] = text.split(".").map(Number);
    return a === 10 || (a === 172 && b >= 16 && b <= 31) || (a === 192 && b === 168) || a === 127 || (a === 169 && b === 254);
  }
  if (v === 6) {
    if (text === "::1") return true;
    const first = text.split(":")[0];
    if (!first) return false;
    const h = parseInt(first, 16);
    return (h & 0xfe00) === 0xfc00 || (h & 0xffc0) === 0xfe80;
  }
  return false;
}

const DOB_CONTEXT_RE = /\b(?:born|dob|date\s+of\s+birth|birthday|birthdate|d\.o\.b)\b/i;

function hasDobContext(text: string, start: number, end: number): boolean {
  const before = 50;
  const after = 30;

  if (before > 0) {
    const regionStart = Math.max(0, start - before);
    if (DOB_CONTEXT_RE.test(text.slice(regionStart, start))) return true;
  }

  if (after > 0) {
    if (DOB_CONTEXT_RE.test(text.slice(end, end + after))) return true;
  }

  return false;
}

function hasPostalContext(text: string, start: number, end: number): boolean {
  const before = 30;
  const regionStart = Math.max(0, start - before);
  if (POSTAL_CONTEXT_RE.test(text.slice(regionStart, start))) return true;
  return false;
}
