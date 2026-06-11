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

const PATTERNS: Array<[PiiType, RegExp]> = [
  [PiiType.EMAIL, /[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}/g],
  [PiiType.PHONE, /(?<![.\d])(?:\+?1[-.\s]?)?(?:\(\d{3}\)|\d{3})[-.\s]?\d{3}[-.\s]?\d{4}(?!\d)/g],
  [PiiType.SSN, /(?<!\d)(?!000|666|9\d\d)\d{3}[-\s](?!00)\d{2}[-\s](?!0000)\d{4}(?!\d)/g],
  [PiiType.CREDIT_CARD, /(?<!\d)(?:4\d{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}|5[1-5]\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}|3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}|6(?:011|5\d{2})[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4})(?!\d)/g],
  [PiiType.IP_ADDRESS, /(?<!\d)(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}(?:25[0-5]|2[0-4]\d|[01]?\d\d?)(?!\d)/g],
  [PiiType.DATE_OF_BIRTH, /\b(?:\d{1,2}[/.\-]\d{1,2}[/.\-]\d{2,4}|\d{4}[/.\-]\d{1,2}[/.\-]\d{1,2}|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4})\b/gi],
  [PiiType.IBAN, /\b[A-Z]{2}\d{2}\s?(?:[A-Z0-9]{4}\s?){2,7}[A-Z0-9]{1,4}\b/gi],
  [PiiType.PASSPORT, /\bpassport[\s_-]*(?:no|number|#|num)?\.?\s*[:.#]\s*\d{9}\b/gi],
  [PiiType.STREET_ADDRESS, /\b\d{1,5}\s+(?:[NSEW]\.?\s+)?(?:[A-Z][a-zA-Z]+\s+){1,3}(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Road|Rd|Court|Ct|Place|Pl|Way|Circle|Cir|Parkway|Pkwy|Highway|Hwy)\.?/gi],
  [PiiType.POSTAL_CODE, /\b(?:\d{5}(?:-\d{4})?|[A-PR-UWYZ][A-HK-Y0-9][AEHMNPRTVXY0-9]?[ABEHMNPRVWXY0-9]?\s*[0-9][ABD-HJLN-UW-Z]{2}|GIR\s*0AA)\b/gi],
  [PiiType.DRIVER_LICENSE, /\b(?:driver'?s?\s+licen[cs]e|dl)\s*(?:no|number|#)?\s*[:.#]?\s*[A-Z0-9-]{5,20}\b/gi],
  [PiiType.AWS_KEY, /\b(?:AKIA|ASIA)[A-Z0-9]{16}\b/g],
];

export function detect(text: string, types?: ReadonlySet<PiiType> | null): Detection[] {
  const detections: Detection[] = [];
  for (const [piiType, pattern] of PATTERNS) {
    if (types && !types.has(piiType)) continue;
    pattern.lastIndex = 0;
    for (const match of text.matchAll(pattern)) {
      const matched = match[0];
      const start = match.index ?? 0;
      if (piiType === PiiType.CREDIT_CARD && !luhnCheck(matched.replace(/\D/g, ""))) continue;
      detections.push({ piiType, start, end: start + matched.length, text: matched });
    }
  }
  return detections.sort((a, b) => a.start - b.start || b.end - a.end);
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
  return digits.length >= 13 && total % 10 === 0;
}
