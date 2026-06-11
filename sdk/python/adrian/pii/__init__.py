"""PII detection and redaction for Adrian events.

Provides standalone ``redact_text()`` for arbitrary strings and
``PiiRedactor`` for redacting ``PairedEvent`` objects.  Redaction
is always on inside ``adrian.init()``: every registered handler is
wrapped in ``RedactingHandler`` automatically, with no opt-out.
"""

from adrian.pii._engine import PiiConfig, RedactionResult, redact_text
from adrian.pii._patterns import Detection, PiiType
from adrian.pii._redactor import PiiRedactor, RedactingHandler
from adrian.pii._strategies import RedactionStrategy

__all__ = [
    "Detection",
    "PiiConfig",
    "PiiRedactor",
    "PiiType",
    "RedactionResult",
    "RedactingHandler",
    "RedactionStrategy",
    "redact_text",
]
