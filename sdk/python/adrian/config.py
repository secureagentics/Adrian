"""Adrian SDK configuration.

Transport (WebSocket, HTTP) is not managed by the SDK, it is handled
by registered ``EventHandler`` implementations. The config holds identity,
logging, and callback settings only.
"""

from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from pathlib import Path
from uuid import uuid4

from adrian.types import EventData, McpServer, VerdictContext

type OnVerdictCallback = (
    Callable[[VerdictContext], None] | Callable[[VerdictContext], Awaitable[None]]
)
"""Callback invoked for every verdict received.

Accepts a ``VerdictContext`` with full event metadata.  May be sync or
async.  Fires for every MAD code the server forwards (M0 / M2 / M3 / M4).
"""

type OnBlockCallback = (
    Callable[[VerdictContext], None] | Callable[[VerdictContext], Awaitable[None]]
)
"""Callback invoked for BLOCK-tier verdicts (M3 / M4).

Accepts a ``VerdictContext``.  May be sync or async.  Return value
is ignored, the block decision is server-driven (via the active
execution mode + per-MAD policy snapshot), so the callback is
notification-only.  For dashboard-mediated approve/reject, configure
the org for HITL mode; the SDK handles resume/halt internally based
on ``Verdict.hitl.continue_execution``.
"""

type OnAuditCallback = (
    Callable[[VerdictContext], None] | Callable[[VerdictContext], Awaitable[None]]
)
"""Callback invoked for NOTIFY (audit) verdicts.

Accepts a ``VerdictContext`` with full event metadata.  May be sync or
async.
"""

type OnEventCallback = (
    Callable[[str, EventData, str, str | None, str | None], None]
    | Callable[[str, EventData, str, str | None, str | None], Awaitable[None]]
)
"""Callback invoked for every paired event emitted by the SDK.

Accepts ``(event_type, data, run_id, parent_run_id, event_id)`` and may
be sync or async.
"""

type OnDisconnectCallback = Callable[[str], None] | Callable[[str], Awaitable[None]]
"""Callback invoked when the WebSocket connection is lost.

Accepts a ``reason`` string (e.g. ``"recv_loop_exit"``, ``"send_failure"``).
Fires once per disconnect, not per reconnect attempt.  May be sync or async.
"""

type OnReconnectCallback = Callable[[], None] | Callable[[], Awaitable[None]]
"""Callback invoked when the WebSocket reconnects after a prior disconnect.

Does not fire on the initial connection.  May be sync or async.
"""

type OnMcpServerCallback = (
    Callable[[McpServer], None] | Callable[[McpServer], Awaitable[None]]
)
"""Callback invoked when an MCP server is registered or its details change.

Receives the freshly-registered ``McpServer``.  May be sync or async.
Fires from both the adapter-layer (``MultiServerMCPClient.__init__``)
and transport-layer (``mcp.client.*_client``) patches.  Does NOT fire
when a re-observation produces an entry identical to the existing one,
so callers see one notification per genuine state change.
"""


@dataclass(slots=True)
class AdrianConfig:
    """Configuration for Adrian event capture.

    Transport is decoupled, handlers manage their own connections.
    This config holds identity, logging, and callback settings.

    Args:
        api_key: Adrian API key.  Format: ``adr_live_xxx`` (production)
            or ``adr_test_xxx`` (dev).  Stored for handlers that need it.
        log_file: Default JSONL output path (used when no handlers are
            explicitly provided to ``init()``).
        log_level: Python logging level name, or ``None`` to inherit
            the application's logging config.
        session_id: Session identifier.  Auto-generated UUID if not set.
        ws_url: WebSocket URL for the Adrian server.  ``None`` disables
            the WebSocket handler.
        block_timeout: Max seconds to wait for a verdict in ``MODE_BLOCK``
            before fail-open.  Ignored in ``MODE_ALERT`` (no wait) and
            ``MODE_HITL`` (wait indefinitely).
        on_event: Callback for every paired event.
        on_verdict: Callback for every verdict.
        on_block: Callback for BLOCK-tier verdicts (M3 / M4).
            Notification-only; return value is ignored.
        on_audit: Callback for NOTIFY-tier verdicts (M2).
        on_disconnect: Callback fired when the WebSocket is lost.  Receives
            a reason string.
        on_reconnect: Callback fired when the WebSocket reconnects after a
            prior disconnect.  Does not fire on the initial connection.
        on_mcp_server: Callback fired each time an MCP server is registered
            or updated.  Does not fire on no-op re-observations.
        replay_buffer_frames: Max number of serialised frames kept in the
            in-memory ring for replay after a transient WS outage.  Each
            frame is one ``ClientFrame.paired_batch`` (~4KB).  Default 1000
            frames ≈ ~4MB RAM.
    """

    api_key: str | None = None
    log_file: Path = field(default_factory=lambda: Path("events.jsonl"))
    log_level: str | None = None
    session_id: str = field(default_factory=lambda: str(uuid4()))
    ws_url: str | None = None
    block_timeout: float = 30.0
    on_event: OnEventCallback | None = None
    on_verdict: OnVerdictCallback | None = None
    on_block: OnBlockCallback | None = None
    on_audit: OnAuditCallback | None = None
    on_disconnect: OnDisconnectCallback | None = None
    on_reconnect: OnReconnectCallback | None = None
    on_mcp_server: OnMcpServerCallback | None = None
    replay_buffer_frames: int = 1000


_config: AdrianConfig | None = None


def get_config() -> AdrianConfig:
    """Return the current global configuration.

    Returns:
        Current AdrianConfig instance.

    Raises:
        RuntimeError: If SDK has not been initialised.
    """
    if _config is None:
        msg = "Adrian SDK has not been initialised. Call adrian.init() first."
        raise RuntimeError(msg)

    return _config


def set_config(config: AdrianConfig | None) -> None:
    """Set or clear the global configuration.

    Args:
        config: Configuration to set, or None to clear.
    """
    global _config  # noqa: PLW0603
    _config = config


def is_initialized() -> bool:
    """Check whether the SDK has been initialised.

    Returns:
        True if ``init()`` has been called.
    """
    return _config is not None
