"""Centralised callback firing helper.

The SDK has a family of optional user-supplied callbacks (``on_event``,
``on_verdict``, ``on_block``, ``on_audit``, ``on_disconnect``,
``on_reconnect``, ``on_mcp_server``).  Each accepts both sync and async
callables, and each must:

- no-op when the user did not configure it,
- swallow and log exceptions so a misbehaving callback can't break the
  caller,
- if the result is a coroutine, run it.

The ``afire`` and ``fire`` helpers in this module collapse that
boilerplate into one well-tested implementation.  Use ``afire`` from an
``async def`` body where you can ``await`` the coroutine result; use
``fire`` from a sync body where you cannot.

This module deliberately has no Adrian dependencies, it's a leaf so
both ``adrian.config``-aware callers and config-free utilities can use
it without a circular-import risk.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from typing import Any

logger = logging.getLogger("adrian.callbacks")


async def afire(
    cb: Callable[..., Any] | None,
    *args: Any,
    name: str = "callback",
) -> Any:  # noqa: ANN401
    """Fire a sync-or-async callback from an async context.

    Awaits coroutine results inline and returns the callback's value
    (or ``None`` when the callback is unset / raises).  Exceptions are
    caught and logged.

    Args:
        cb: The callback to invoke.  If ``None``, this is a no-op.
        *args: Positional arguments to pass to the callback.
        name: Human-readable name used in the exception log.

    Returns:
        The callback's return value (after awaiting if it returned a
        coroutine), or ``None`` on absent callback / exception.
    """
    if cb is None:
        return None

    try:
        result = cb(*args)
    except Exception:
        logger.exception("%s raised", name)
        return None

    if asyncio.iscoroutine(result):
        try:
            return await result
        except Exception:
            logger.exception("%s coroutine raised", name)
            return None

    return result


def fire(
    cb: Callable[..., Any] | None,
    *args: Any,
    name: str = "callback",
) -> None:
    """Fire a sync-or-async callback from a sync context.

    Sync callbacks run inline.  Coroutine results are scheduled
    fire-and-forget on the running event loop; if no loop is running
    the coroutine is closed with a warning.  Exceptions raised by the
    callback are caught and logged so a misbehaving callback can't
    break the caller.

    Args:
        cb: The callback to invoke.  If ``None``, this is a no-op.
        *args: Positional arguments to pass to the callback.
        name: Human-readable name used in log messages.
    """
    if cb is None:
        return

    try:
        result = cb(*args)
    except Exception:
        logger.exception("%s raised", name)
        return

    if not asyncio.iscoroutine(result):
        return

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        logger.warning(
            "%s returned a coroutine but no event loop is running; "
            "async callback dropped",
            name,
        )
        result.close()
        return

    loop.create_task(result)


__all__ = ["afire", "fire"]
