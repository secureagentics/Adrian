# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""PII detection and redaction for Claude Code events.

Provides standalone ``redact_text()`` for arbitrary strings and
``redact_event()`` for protobuf ``PairedEvent`` messages.  Redaction is
always on: ``agent._ws_send_event`` redacts every event before it is
serialised, with no opt-out, matching the SDK where ``adrian.init()``
wraps every handler in ``RedactingHandler``.

The detection engine is vendored from ``sdk/python/adrian/pii``; see
``_patterns.py``.  The redactor is plugin-specific because the plugin
speaks protobuf directly rather than the SDK's dataclasses.
"""

from adrian_cc.pii._engine import PiiConfig, RedactionResult, redact_text
from adrian_cc.pii._patterns import Detection, PiiType
from adrian_cc.pii._redactor import PiiRedactor, redact_event
from adrian_cc.pii._strategies import RedactionStrategy

__all__ = [
    "Detection",
    "PiiConfig",
    "PiiRedactor",
    "PiiType",
    "RedactionResult",
    "RedactionStrategy",
    "redact_event",
    "redact_text",
]
