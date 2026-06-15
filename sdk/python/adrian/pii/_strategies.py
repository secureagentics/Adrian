"""PII redaction strategies.

Each strategy transforms a ``Detection``'s matched text into a
replacement string.  Strategies are stateless functions selected
by the ``RedactionStrategy`` enum.
"""

from __future__ import annotations

import hashlib
import re
from enum import Enum

from adrian.pii._patterns import Detection, PiiType


class RedactionStrategy(Enum):
    """Available redaction strategies."""

    REPLACE = "replace"
    MASK = "mask"
    HASH = "hash"


def apply_strategy(detection: Detection, strategy: RedactionStrategy) -> str:
    """Return the replacement string for a detection.

    Args:
        detection: The PII detection to redact.
        strategy: Which redaction strategy to apply.

    Returns:
        Replacement string.
    """
    match strategy:
        case RedactionStrategy.REPLACE:
            return _replace(detection)
        case RedactionStrategy.MASK:
            return _mask(detection)
        case RedactionStrategy.HASH:
            return _hash(detection)


def _replace(detection: Detection) -> str:
    """Replace with a type tag like ``[EMAIL_REDACTED]``."""
    return f"[{detection.pii_type.value}_REDACTED]"


def _hash(detection: Detection) -> str:
    """Replace with a type-prefixed short hash."""
    digest = hashlib.sha256(detection.text.encode()).hexdigest()[:8]

    return f"[{detection.pii_type.value}:{digest}]"


def _mask(detection: Detection) -> str:
    """Type-aware masking that preserves structural hints."""
    match detection.pii_type:
        case PiiType.EMAIL:
            return _mask_email(detection.text)
        case PiiType.PHONE:
            return _mask_phone(detection.text)
        case PiiType.SSN:
            return _mask_ssn(detection.text)
        case PiiType.CREDIT_CARD:
            return _mask_credit_card(detection.text)
        case PiiType.IP_ADDRESS:
            return _mask_ip(detection.text)
        case _:
            return _mask_generic(detection.text)


def _mask_email(text: str) -> str:
    """Mask email: ``j***@***.com``."""
    parts = text.split("@", 1)

    if len(parts) != 2:
        return _mask_generic(text)

    local, domain = parts
    domain_parts = domain.rsplit(".", 1)

    if len(domain_parts) != 2:
        return _mask_generic(text)

    masked_local = local[0] + "***" if local else "***"
    masked_domain = "***." + domain_parts[1]

    return f"{masked_local}@{masked_domain}"


def _mask_phone(text: str) -> str:
    """Mask phone: preserve last 4 digits."""
    digits = re.sub(r"\D", "", text)

    if len(digits) < 4:
        return "***"

    return "***-***-" + digits[-4:]


def _mask_ssn(text: str) -> str:
    """Mask SSN: ``***-**-6789``."""
    digits = re.sub(r"\D", "", text)

    if len(digits) < 4:
        return "***-**-****"

    return "***-**-" + digits[-4:]


def _mask_credit_card(text: str) -> str:
    """Mask credit card: ``****-****-****-1234``."""
    digits = re.sub(r"\D", "", text)

    if len(digits) < 4:
        return "****-****-****-****"

    return "****-****-****-" + digits[-4:]


def _mask_ip(text: str) -> str:
    """Mask IP address: ``***.***.***.123`` for IPv4."""
    if ":" in text:
        # IPv6: mask all but last group
        parts = text.split(":")
        masked = ["****"] * (len(parts) - 1) + [parts[-1]]

        return ":".join(masked)

    # IPv4
    parts = text.split(".")

    if len(parts) == 4:
        return f"***.***.***.{parts[3]}"

    return _mask_generic(text)


def _mask_generic(text: str) -> str:
    """Generic mask: first and last char visible, middle starred."""
    if len(text) <= 2:
        return "*" * len(text)

    return text[0] + "*" * (len(text) - 2) + text[-1]
