"""MCP server tracking for LangChain agents.

Maintains a per-process aggregate of the MCP servers an agent is
talking to, captured via two layers of hooks:

- **Adapter layer**, ``MultiServerMCPClient.__init__`` from
  ``langchain-mcp-adapters``.  Snapshots the declared topology with
  user-supplied server names at construction.
- **Transport layer**, the four ``mcp.client.*`` transport-opening
  functions (``stdio_client``, ``streamablehttp_client``,
  ``sse_client``, ``websocket_client``).  Catches every actual
  transport open, including raw-``mcp`` usage that bypasses the
  adapter (``async with stdio_client(...) as ...``).

The transport-layer hooks subsume what an adapter-layer
``session()`` / ``get_tools()`` patch would have given us, every
adapter session ultimately funnels through ``mcp.client.*``, so we
don't patch those.  The trade-off is that the transport layer never
sees the user-supplied name, so its registrations use a synthesised
``"<transport>:<endpoint>"`` form.  The dedup rule in
``_register_synthesised`` stops a synthesised entry from overwriting
an existing user-named entry for the same ``(transport, endpoint)``.

"""

from __future__ import annotations

import contextlib
import logging
import sys
from collections.abc import Mapping
from typing import Any, cast

from adrian.callbacks import fire
from adrian.config import get_config, is_initialized
from adrian.types import McpServer

logger = logging.getLogger("adrian.mcp")

# Re-export ``McpServer`` so existing callers can continue to import it
# from ``adrian.mcp`` even though the canonical home is ``adrian.types``.
__all__ = ["McpServer", "mcp_servers"]


_servers: dict[str, McpServer] = {}


def mcp_servers() -> list[McpServer]:
    """Return a snapshot of the currently observed MCP servers.

    Order is by first-observed; later observations of the same name
    update the entry in place but do not change ordering.
    """
    return list(_servers.values())


def _reset() -> None:  # pyright: ignore[reportUnusedFunction]
    """Drop all observed servers.  Called from fork / shutdown handlers."""
    _servers.clear()


def _set(server: McpServer) -> None:
    """Single mutation point for ``_servers``; fires ``on_mcp_server`` on change.

    Compares against the existing entry under the same name and skips
    both the write and the callback if nothing changed, so callers
    see one notification per genuine state change, not per
    re-observation.
    """
    previous = _servers.get(server.name)
    if previous == server:
        return

    _servers[server.name] = server
    _fire_on_mcp_server(server)


def _register(name: str, connection: Any) -> None:  # noqa: ANN401
    """Record an observed server under a known (user-supplied) name.

    Last-write-wins: a second observation of the same name overwrites
    the previous entry, so transport or endpoint changes propagate.
    """
    if not name:
        return

    _set(_server_from_connection(name, connection))


def _register_synthesised(transport: str, endpoint: str) -> None:
    """Record a transport-layer observation with a synthesised name.

    Skipped when an existing entry already has the same
    ``(transport, endpoint)`` under any other name, the existing
    entry's name is more meaningful (it came from the adapter layer,
    where the user supplied it) and we don't want to clutter the
    registry with a parallel synthesised duplicate.
    """
    if not endpoint and transport == "unknown":
        return

    for existing in _servers.values():
        if existing.transport == transport and existing.endpoint == endpoint:
            return

    name = f"{transport}:{endpoint}" if endpoint else transport
    _set(McpServer(name=name, transport=transport, endpoint=endpoint))


def _fire_on_mcp_server(server: McpServer) -> None:
    """Invoke the configured ``on_mcp_server`` callback, if any.

    Silent no-op when the SDK has not been initialised, the registry
    can populate before ``init()`` runs (e.g. tests that import the
    adapter library before configuring Adrian), and we don't want a
    ``RuntimeError`` from ``get_config()`` to bubble up from a
    transport patch.
    """
    if not is_initialized():
        return

    config = get_config()
    fire(config.on_mcp_server, server, name="on_mcp_server")


def _server_from_connection(name: str, connection: Any) -> McpServer:  # noqa: ANN401
    """Convert a ``Connection`` mapping into an ``McpServer``."""
    if not isinstance(connection, Mapping):
        return McpServer(name=name, transport="unknown", endpoint="")

    conn = cast("Mapping[str, Any]", connection)
    transport = str(conn.get("transport") or "").lower() or "unknown"
    endpoint = _endpoint_for(transport, conn)

    return McpServer(name=name, transport=transport, endpoint=endpoint)


def _endpoint_for(transport: str, connection: Mapping[str, Any]) -> str:
    """Extract a transport-specific endpoint string from a connection dict.

    For URL-based transports (sse, streamable_http variants, websocket)
    returns the URL.  For stdio returns the joined command line.  For
    anything else returns an empty string.
    """
    if transport == "stdio":
        command = str(connection.get("command", ""))
        raw_args: Any = connection.get("args") or []

        if not isinstance(raw_args, (list, tuple)):
            return command

        args: list[Any] = list(raw_args)  # pyright: ignore[reportUnknownArgumentType]
        parts = [command, *(str(a) for a in args)]
        return " ".join(p for p in parts if p)

    # URL-bearing transports.  streamable_http has historically used
    # several spellings of the transport key, match all of them.
    if transport in {"sse", "websocket", "streamable_http", "streamable-http", "http"}:
        return str(connection.get("url", ""))

    return ""


# ------------------------------------------------------------------
# Patching
# ------------------------------------------------------------------


def _patch_mcp_adapter() -> None:  # pyright: ignore[reportUnusedFunction]
    """Apply the adapter-layer and transport-layer patches.

    All patches are idempotent and silently no-op when their target
    library is not installed.
    """
    _patch_langchain_mcp_adapters()
    _patch_mcp_transports()


def _patch_langchain_mcp_adapters() -> None:
    """Patch ``MultiServerMCPClient.__init__`` to snapshot declarations."""
    try:
        from langchain_mcp_adapters import client as client_mod
    except ImportError:
        return

    cls = getattr(client_mod, "MultiServerMCPClient", None)

    if cls is None or getattr(cls, "_adrian_mcp_patched", False):
        return

    original_init = cls.__init__

    def patched_init(self: Any, *args: Any, **kwargs: Any) -> None:  # noqa: ANN401
        original_init(self, *args, **kwargs)
        _register_all_from_client(self)

    cls.__init__ = patched_init
    cls._adrian_mcp_patched = True
    logger.debug("Patched MultiServerMCPClient.__init__")


def _patch_mcp_transports() -> None:
    """Patch the four ``mcp.client.*`` transport-opening functions.

    Each is decorated with ``@asynccontextmanager``; we wrap them so
    registration happens *synchronously* on call (before the context
    manager body runs) and the original return value is forwarded.

    The library's submodules each ``from .stdio import stdio_client``
    style, they hold their own bindings, so we rebind every loaded
    module that imports the symbol by name.
    """
    # ``mcp.client.streamable_http`` exposes both spellings:
    # ``streamablehttp_client`` (old, deprecated) and
    # ``streamable_http_client`` (new).  They are distinct function
    # objects; we patch both since callers exist for each.
    transports: list[tuple[str, str, str]] = [
        ("mcp.client.stdio", "stdio_client", "stdio"),
        ("mcp.client.streamable_http", "streamablehttp_client", "streamable_http"),
        ("mcp.client.streamable_http", "streamable_http_client", "streamable_http"),
        ("mcp.client.sse", "sse_client", "sse"),
        ("mcp.client.websocket", "websocket_client", "websocket"),
    ]

    for mod_name, attr, kind in transports:
        try:
            mod = __import__(mod_name, fromlist=[attr])
        except ImportError:
            continue

        original = getattr(mod, attr, None)

        if original is None or getattr(original, "_adrian_mcp_patched", False):
            continue

        wrapper = _make_transport_wrapper(original, kind)
        _rebind_symbol(attr, original, wrapper)
        logger.debug("Patched %s.%s", mod_name, attr)


def _make_transport_wrapper(original: Any, kind: str) -> Any:  # noqa: ANN401
    """Build the wrapper that registers then forwards to ``original``.

    ``kind`` is the transport string ("stdio", "sse", ...) used to
    label the synthesised registry entry.
    """

    def patched(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
        try:
            endpoint = _endpoint_from_transport_args(kind, args, kwargs)
        except Exception:
            logger.exception("Failed to extract MCP %s endpoint", kind)
            endpoint = ""

        _register_synthesised(kind, endpoint)

        return original(*args, **kwargs)

    patched._adrian_mcp_patched = True  # type: ignore[attr-defined]
    return patched


def _endpoint_from_transport_args(
    kind: str,
    args: tuple[Any, ...],
    kwargs: dict[str, Any],
) -> str:
    """Pull the endpoint string out of a transport function's call args.

    - ``stdio_client(server: StdioServerParameters)``, first positional
      is a pydantic model with ``.command`` and ``.args``.
    - URL transports, first positional or ``url=`` kwarg is a string.
    """
    if kind == "stdio":
        params = kwargs.get("server")

        if params is None and args:
            params = args[0]

        if params is None:
            return ""

        command = str(getattr(params, "command", "") or "")
        raw_args: Any = getattr(params, "args", None) or []

        if not isinstance(raw_args, (list, tuple)):
            return command

        items: list[Any] = list(raw_args)  # pyright: ignore[reportUnknownArgumentType]
        parts = [command, *(str(a) for a in items)]
        return " ".join(p for p in parts if p)

    # URL transports
    url = kwargs.get("url")

    if url is None and args:
        url = args[0]

    return str(url) if isinstance(url, str) else ""


def _rebind_symbol(attr: str, original: Any, replacement: Any) -> None:  # noqa: ANN401
    """Replace ``attr`` in every loaded module that bound it to ``original``.

    Submodules of ``mcp`` and ``langchain_mcp_adapters`` each
    ``from .stdio import stdio_client`` (etc.) at import time, so each
    holds its own reference.  Patching the canonical location alone
    leaves callers in other modules invoking the unwrapped original.
    """
    for mod in list(sys.modules.values()):
        # sys.modules can hold None entries to signal failed imports;
        # reportUnnecessaryComparison thinks ModuleType is the only
        # possible type but in practice None does appear.
        if mod is None:  # pyright: ignore[reportUnnecessaryComparison]
            continue

        bound = getattr(mod, attr, None)
        if bound is original:
            # Some modules disallow attribute writes; skip those.
            with contextlib.suppress(AttributeError, TypeError):
                setattr(mod, attr, replacement)


# ------------------------------------------------------------------
# Adapter-side helpers
# ------------------------------------------------------------------


def _client_connections(client: Any) -> Mapping[str, Any] | None:  # noqa: ANN401
    """Return the client's connections mapping, handling 0.1.x vs 0.2.x.

    0.2.x exposes the public attribute ``connections``.  0.1.x had it
    underscored as ``_connections``.  Either is accepted.
    """
    raw = getattr(client, "connections", None)

    if raw is None:
        raw = getattr(client, "_connections", None)

    if not isinstance(raw, Mapping):
        return None

    return cast("Mapping[str, Any]", raw)


def _register_all_from_client(client: Any) -> None:  # noqa: ANN401
    """Register every entry in ``client.connections``."""
    connections = _client_connections(client)

    if connections is None:
        return

    for name, conn in connections.items():
        try:
            _register(name, conn)
        except Exception:
            logger.exception("Failed to register MCP server %r", name)
