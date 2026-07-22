# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Standalone PII detection and redaction engine.

Provides ``redact_text``, usable independently of the Adrian SDK.
Zero dependencies beyond Python stdlib and sibling pattern/strategy
modules.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from adrian.pii._patterns import Detection, PiiType, detect
from adrian.pii._strategies import RedactionStrategy, apply_strategy


@dataclass(slots=True)
class PiiConfig:
    """Configuration for PII redaction.

    Attributes:
        strategy: Redaction strategy to apply.
        enabled_types: PII types to detect.  ``None`` means all.
    """

    strategy: RedactionStrategy = RedactionStrategy.REPLACE
    enabled_types: frozenset[PiiType] | None = None


@dataclass(slots=True)
class RedactionResult:
    """Result of a ``redact_text`` call.

    Attributes:
        text: The redacted text.
        detections: All PII detections found and redacted.
    """

    text: str
    detections: list[Detection] = field(default_factory=list)


def redact_text(
    text: str,
    config: PiiConfig | None = None,
) -> RedactionResult:
    """Detect and redact PII in a text string.

    This is the standalone entry point, usable without initialising
    the Adrian SDK.

    Args:
        text: Input text to scan and redact.
        config: Redaction configuration.  Defaults to ``REPLACE``
            strategy with all types enabled.

    Returns:
        ``RedactionResult`` with redacted text and detection list.
    """
    if not text:
        return RedactionResult(text=text)

    cfg = config or PiiConfig()

    detections = detect(text, types=cfg.enabled_types)

    if not detections:
        return RedactionResult(text=text)

    # Replace right-to-left to preserve character indices
    result = text

    for det in reversed(detections):
        replacement = apply_strategy(det, cfg.strategy)
        result = result[: det.start] + replacement + result[det.end :]

    return RedactionResult(text=result, detections=detections)
