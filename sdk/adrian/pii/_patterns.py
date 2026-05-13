"""PII detection patterns.

Compiled regex patterns for common PII types found in AI agent
conversations.  All patterns are compiled at module import time
for zero per-call compilation overhead.
"""

from __future__ import annotations

import ipaddress
import re
from dataclasses import dataclass
from enum import Enum


class PiiType(Enum):
    """Supported PII categories."""

    EMAIL = "EMAIL"
    PHONE = "PHONE"
    SSN = "SSN"
    CREDIT_CARD = "CREDIT_CARD"
    IP_ADDRESS = "IP_ADDRESS"
    DATE_OF_BIRTH = "DATE_OF_BIRTH"
    IBAN = "IBAN"
    PASSPORT = "PASSPORT"
    STREET_ADDRESS = "STREET_ADDRESS"
    POSTAL_CODE = "POSTAL_CODE"
    DRIVER_LICENSE = "DRIVER_LICENSE"
    AWS_KEY = "AWS_KEY"


@dataclass(slots=True)
class Detection:
    """A single PII detection within a text.

    Attributes:
        pii_type: The category of PII detected.
        start: Start character index (inclusive).
        end: End character index (exclusive).
        text: The matched text.
    """

    pii_type: PiiType
    start: int
    end: int
    text: str


# ------------------------------------------------------------------
# Luhn checksum for credit card validation
# ------------------------------------------------------------------


def _luhn_check(digits: str) -> bool:
    """Validate a digit string with the Luhn algorithm.

    Args:
        digits: String of digits only (no separators).

    Returns:
        True if the checksum is valid.
    """
    total = 0
    reverse = digits[::-1]

    for i, ch in enumerate(reverse):
        n = int(ch)

        if i % 2 == 1:
            n *= 2

            if n > 9:
                n -= 9

        total += n

    return total % 10 == 0


# ------------------------------------------------------------------
# Context-word helpers
# ------------------------------------------------------------------

_DOB_CONTEXT_RE = re.compile(
    r"(?:born|dob|date\s+of\s+birth|birthday|birthdate|d\.o\.b)",
    re.IGNORECASE,
)

# Postal-code context: only the literal keywords. The previous regex
# also accepted bare 2-letter US state codes under IGNORECASE, which
# false-positived on common English words like "in", "or", "me", "ok".
_POSTAL_CONTEXT_RE = re.compile(
    r"(?:zip(?:\s*code)?|postal(?:\s*code)?)",
    re.IGNORECASE,
)


def _has_context(
    text: str,
    start: int,
    end: int,
    pattern: re.Pattern[str],
    before: int = 50,
    after: int = 0,
) -> bool:
    """Check for context keywords near a match.

    Args:
        text: Full input text.
        start: Start index of the match.
        end: End index of the match.
        pattern: Compiled regex of context keywords.
        before: Characters before the match to search.
        after: Characters after the match to search.

    Returns:
        True if a context keyword is found in either window.
    """
    if before > 0:
        region_start = max(0, start - before)
        if pattern.search(text[region_start:start]) is not None:
            return True

    if after > 0:
        region = text[end : end + after]
        if pattern.search(region) is not None:
            return True

    return False


def _is_private_ip(text: str) -> bool:
    """Return True for private, loopback, or link-local IPs.

    Public/global addresses are rejected so the IP detector only
    flags internal-network addresses (RFC 1918 + loopback +
    link-local; IPv6 fc00::/7, fe80::/10, ::1).
    """
    try:
        ip = ipaddress.ip_address(text)
    except ValueError:
        return False

    return ip.is_private or ip.is_loopback or ip.is_link_local


# ------------------------------------------------------------------
# Compiled patterns
# ------------------------------------------------------------------

_EMAIL_RE = re.compile(
    r"[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}",
)

_PHONE_RE = re.compile(
    r"(?<![.\d])"
    r"(?:\+?1[-.\s]?)?"
    r"(?:\(\d{3}\)|\d{3})[-.\s]?"
    r"\d{3}[-.\s]?\d{4}"
    r"(?!\d)",
)

_SSN_RE = re.compile(
    r"(?<!\d)"
    r"(?!000|666|9\d\d)\d{3}"
    r"[-\s]"
    r"(?!00)\d{2}"
    r"[-\s]"
    r"(?!0000)\d{4}"
    r"(?!\d)",
)

_CREDIT_CARD_RE = re.compile(
    r"(?<!\d)"
    r"(?:"
    # Visa: 4XXX XXXX XXXX XXXX (16 digits)
    r"4\d{3}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"
    r"|"
    # Mastercard: 5[1-5]XX XXXX XXXX XXXX (16 digits)
    r"5[1-5]\d{2}[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"
    r"|"
    # Amex: 3[47]XX XXXXXX XXXXX (15 digits)
    r"3[47]\d{2}[-\s]?\d{6}[-\s]?\d{5}"
    r"|"
    # Discover: 6011 XXXX XXXX XXXX or 65XX (16 digits)
    r"6(?:011|5\d{2})[-\s]?\d{4}[-\s]?\d{4}[-\s]?\d{4}"
    r")"
    r"(?!\d)",
)

_IPV4_RE = re.compile(
    r"(?<!\d)"
    r"(?:(?:25[0-5]|2[0-4]\d|[01]?\d\d?)\.){3}"
    r"(?:25[0-5]|2[0-4]\d|[01]?\d\d?)"
    r"(?!\d)",
)

_IPV6_RE = re.compile(
    r"(?:[0-9a-fA-F]{1,4}:){7}[0-9a-fA-F]{1,4}"
    r"|(?:[0-9a-fA-F]{1,4}:){1,7}:"
    r"|(?:[0-9a-fA-F]{1,4}:){1,6}:[0-9a-fA-F]{1,4}"
    r"|::(?:[0-9a-fA-F]{1,4}:){0,5}[0-9a-fA-F]{1,4}"
    r"|::1",
)

_DATE_RE = re.compile(
    r"\b(?:"
    r"\d{1,2}[/\-.]\d{1,2}[/\-.]\d{2,4}"
    r"|\d{4}[/\-.]\d{1,2}[/\-.]\d{1,2}"
    r"|(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May|Jun(?:e)?"
    r"|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?|Nov(?:ember)?"
    r"|Dec(?:ember)?)\s+\d{1,2},?\s+\d{4}"
    r"|\d{1,2}\s+(?:Jan(?:uary)?|Feb(?:ruary)?|Mar(?:ch)?|Apr(?:il)?|May"
    r"|Jun(?:e)?|Jul(?:y)?|Aug(?:ust)?|Sep(?:tember)?|Oct(?:ober)?"
    r"|Nov(?:ember)?|Dec(?:ember)?)\s+\d{4}"
    r")\b",
    re.IGNORECASE,
)

_IBAN_RE = re.compile(
    r"\b[A-Z]{2}\d{2}\s?(?:[A-Z0-9]{4}\s?){2,7}[A-Z0-9]{1,4}\b",
    re.IGNORECASE,
)

_PASSPORT_RE = re.compile(
    r"\bpassport"
    r"[\s_\-]*(?:no|number|#|num)?\.?\s*[:.#]\s*"
    r"(\d{9})\b",
    re.IGNORECASE,
)

_STREET_ADDRESS_RE = re.compile(
    r"\b\d{1,5}\s+"
    r"(?:[NSEW]\.?\s+)?"
    r"(?:[A-Z][a-zA-Z]+\s+){1,3}"
    r"(?:Street|St|Avenue|Ave|Boulevard|Blvd|Drive|Dr|Lane|Ln|Road|Rd"
    r"|Court|Ct|Place|Pl|Way|Circle|Cir|Parkway|Pkwy|Highway|Hwy)"
    r"\.?"
    r"(?:\s+(?:Apt|Suite|Ste|Unit|#)\s*\.?\s*\d+[A-Za-z]?)?",
    re.IGNORECASE,
)

# US ZIP: 5 digits or 5+4. Requires a "zip"/"postal" keyword nearby
# (post-filtered) to avoid matching arbitrary 5-digit numbers.
_US_ZIP_RE = re.compile(
    r"(?<!\d)\d{5}(?:-\d{4})?(?!\d)",
)

# UK postcode: tight character classes per Royal Mail grammar.
# - Outward: 1 letter (no QVX) + letter/digit + optional letter/digit ×2
# - Separator: zero or more spaces (real serializations sometimes omit)
# - Inward: 1 digit + 2 letters (no CIKMV)
# Plus the special GIR 0AA. \b anchors keep matches inline-safe.
_UK_POSTCODE_RE = re.compile(
    r"\b(?:"
    r"[A-PR-UWYZ][A-HK-Y0-9][AEHMNPRTVXY0-9]?[ABEHMNPRVWXY0-9]?"
    r"\s*"
    r"[0-9][ABD-HJLN-UW-Z]{2}"
    r"|GIR\s*0AA"
    r")\b",
    re.IGNORECASE,
)

_DRIVER_LICENSE_RE = re.compile(
    r"(?:driver'?s?\s+licen[cs]e|\bDL|\bD\.L\.)"
    r"\s*(?:no|number|#|num)?\.?\s*[:.#]\s*"
    r"([A-Z0-9]{5,15})\b",
    re.IGNORECASE,
)

_AWS_KEY_RE = re.compile(
    r"\bAKIA[0-9A-Z]{16}\b",
)

# ------------------------------------------------------------------
# Pattern registry
# ------------------------------------------------------------------

# Each entry is (PiiType, compiled_pattern, needs_group, post_filter).
# needs_group: if True, use group(1) as the matched text (for patterns
# with a prefix keyword like "passport").
# post_filter: tag selecting a validation step in detect(); None to skip.

type _PostFilter = None | str

_PATTERN_REGISTRY: list[tuple[PiiType, re.Pattern[str], bool, _PostFilter]] = [
    (PiiType.EMAIL, _EMAIL_RE, False, None),
    (PiiType.PHONE, _PHONE_RE, False, None),
    (PiiType.SSN, _SSN_RE, False, None),
    (PiiType.CREDIT_CARD, _CREDIT_CARD_RE, False, "luhn"),
    (PiiType.IP_ADDRESS, _IPV4_RE, False, "private_ip"),
    (PiiType.IP_ADDRESS, _IPV6_RE, False, "private_ip"),
    (PiiType.DATE_OF_BIRTH, _DATE_RE, False, "dob_context"),
    (PiiType.IBAN, _IBAN_RE, False, None),
    (PiiType.PASSPORT, _PASSPORT_RE, True, None),
    (PiiType.STREET_ADDRESS, _STREET_ADDRESS_RE, False, None),
    (PiiType.POSTAL_CODE, _US_ZIP_RE, False, "postal_context"),
    (PiiType.POSTAL_CODE, _UK_POSTCODE_RE, False, None),
    (PiiType.DRIVER_LICENSE, _DRIVER_LICENSE_RE, True, None),
    (PiiType.AWS_KEY, _AWS_KEY_RE, False, None),
]


# ------------------------------------------------------------------
# Detection engine
# ------------------------------------------------------------------


def detect(
    text: str,
    types: frozenset[PiiType] | None = None,
) -> list[Detection]:
    """Scan text for PII matches.

    Runs all enabled patterns, resolves overlaps (leftmost match
    wins), and returns sorted, non-overlapping detections.

    Args:
        text: Input text to scan.
        types: Subset of PII types to detect.  ``None`` means all.

    Returns:
        List of detections sorted by start position.
    """
    if not text:
        return []

    raw: list[Detection] = []

    for pii_type, pattern, needs_group, post_filter in _PATTERN_REGISTRY:
        if types is not None and pii_type not in types:
            continue

        for m in pattern.finditer(text):
            if needs_group and m.lastindex and m.lastindex >= 1:
                matched_text = m.group(1)
                start = m.start(1)
                end = m.end(1)
            else:
                matched_text = m.group(0)
                start = m.start()
                end = m.end()

            if not _passes_post_filter(post_filter, text, m, matched_text):
                continue

            raw.append(
                Detection(
                    pii_type=pii_type,
                    start=start,
                    end=end,
                    text=matched_text,
                )
            )

    return _resolve_overlaps(raw)


def _passes_post_filter(
    post_filter: _PostFilter,
    text: str,
    match: re.Match[str],
    matched_text: str,
) -> bool:
    """Run the per-pattern validation step, if any."""
    match post_filter:
        case None:
            return True
        case "luhn":
            digits = re.sub(r"[\s\-]", "", matched_text)
            return _luhn_check(digits)
        case "dob_context":
            return _has_context(
                text,
                match.start(),
                match.end(),
                _DOB_CONTEXT_RE,
                before=50,
                after=30,
            )
        case "postal_context":
            return _has_context(
                text,
                match.start(),
                match.end(),
                _POSTAL_CONTEXT_RE,
                before=30,
                after=0,
            )
        case "private_ip":
            return _is_private_ip(matched_text)
        case _:
            return True


def _resolve_overlaps(detections: list[Detection]) -> list[Detection]:
    """Remove overlapping detections, keeping the leftmost match.

    Detections are sorted by ``start`` ascending; ties broken by
    longer match first.  A greedy left-to-right pass then keeps the
    earliest-starting detection at each position and drops anything
    that overlaps it.  Note that this is **not** a global
    longest-match-wins algorithm: when detection A is shorter than
    detection B but starts earlier, A wins even if B is longer.

    Args:
        detections: Unsorted list of raw detections.

    Returns:
        Sorted, non-overlapping list.
    """
    if not detections:
        return []

    # Sort by start position, then by length descending (longest first)
    detections.sort(key=lambda d: (d.start, -(d.end - d.start)))

    result: list[Detection] = []
    last_end = -1

    for det in detections:
        if det.start >= last_end:
            result.append(det)
            last_end = det.end

    return result
