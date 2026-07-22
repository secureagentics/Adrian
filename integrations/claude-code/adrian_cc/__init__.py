# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Adrian for Claude Code - runtime security monitoring via hooks.

Intercepts Claude Code tool calls through the native hooks system,
streams events to the Adrian backend over WebSocket using the same
protobuf event format as the LangChain SDK, and enforces verdicts
(block / audit / allow) in real-time.
"""

__version__ = "0.1.0"
