# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Handler hook registry.

Decouples event transport from event capture. The SDK captures and
pairs events, then emits ``PairedEvent`` objects through registered
handlers. Multiple handlers fire for every event.

Built-in handlers:
- ``JSONLHandler``, writes paired events to a JSONL file
- (future) ``WebSocketHandler``, streams to worker core API

Users can register custom handlers for their own backends.
"""

from __future__ import annotations

import logging
from typing import Protocol, runtime_checkable

from adrian.format.types import PairedEvent

logger = logging.getLogger("adrian.hooks")


@runtime_checkable
class EventHandler(Protocol):
    """Protocol for handling paired events.

    Implement this to create a custom handler. Register instances
    via ``HookRegistry.register()`` or pass them to ``adrian.init()``.
    """

    async def on_paired_event(self, event: PairedEvent) -> None:
        """Handle a single paired event.

        Args:
            event: The assembled paired event.
        """
        ...

    async def close(self) -> None:
        """Clean up resources on shutdown."""
        ...


class HookRegistry:
    """Registry of event handlers.

    All registered handlers fire for every ``PairedEvent``. Errors in
    one handler are logged but do not prevent other handlers from
    firing.
    """

    def __init__(self) -> None:
        """Initialise with an empty handler list."""
        self._handlers: list[EventHandler] = []

    def register(self, handler: EventHandler) -> None:
        """Add a handler to the registry.

        Args:
            handler: Handler instance implementing ``EventHandler``.
        """
        self._handlers.append(handler)

    def __len__(self) -> int:
        """Return the number of registered handlers."""
        return len(self._handlers)

    async def emit(self, event: PairedEvent) -> None:
        """Emit a paired event to all registered handlers.

        Each handler is called in registration order. If a handler
        raises an exception, it is logged and the remaining handlers
        still fire.

        Args:
            event: The paired event to emit.
        """
        for handler in self._handlers:
            try:
                await handler.on_paired_event(event)
            except Exception:
                logger.exception(
                    "handler %s failed on event %s",
                    type(handler).__name__,
                    event.event_id,
                )

    async def close(self) -> None:
        """Close all registered handlers.

        Errors during close are logged but do not propagate.
        """
        for handler in self._handlers:
            try:
                await handler.close()
            except Exception:
                logger.exception(
                    "handler %s failed to close",
                    type(handler).__name__,
                )
