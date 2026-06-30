"""Tests for adrian.mcp, MCP server tracking via langchain-mcp-adapters."""

from __future__ import annotations

import asyncio
import warnings
from collections.abc import Iterator
from typing import Any

# pyright: reportPrivateUsage=false
import adrian
import mcp.client.stdio as mcp_stdio_mod
import pytest
from adrian import mcp as adrian_mcp
from adrian.mcp import (
    McpServer,
    _endpoint_for,
    _patch_mcp_adapter,
    _register,
    _reset,
    _server_from_connection,
    mcp_servers,
)
from langchain_mcp_adapters import sessions as adapter_sessions
from langchain_mcp_adapters.client import MultiServerMCPClient
from mcp.client.sse import sse_client
from mcp.client.stdio import stdio_client
from mcp.client.streamable_http import (
    streamable_http_client,
    streamablehttp_client,  # pyright: ignore[reportDeprecated]
)
from mcp.client.websocket import websocket_client

from mcp import StdioServerParameters


@pytest.fixture(autouse=True)
def _isolate_state() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Each test starts with an empty registry."""
    _reset()
    yield
    _reset()


# ------------------------------------------------------------------
# Pure helpers
# ------------------------------------------------------------------


class TestEndpointFormatting:
    def test_stdio_joins_command_and_args(self) -> None:
        ep = _endpoint_for("stdio", {"command": "npx", "args": ["-y", "@x/server"]})
        assert ep == "npx -y @x/server"

    def test_stdio_command_only(self) -> None:
        assert (
            _endpoint_for("stdio", {"command": "/usr/local/bin/server"})
            == "/usr/local/bin/server"
        )

    def test_stdio_no_command(self) -> None:
        assert _endpoint_for("stdio", {}) == ""

    def test_sse_url(self) -> None:
        assert (
            _endpoint_for("sse", {"url": "https://mcp.example/sse"})
            == "https://mcp.example/sse"
        )

    def test_streamable_http_url(self) -> None:
        assert (
            _endpoint_for("streamable_http", {"url": "https://mcp.example/"})
            == "https://mcp.example/"
        )

    def test_streamable_http_dash_alias(self) -> None:
        assert _endpoint_for("streamable-http", {"url": "https://x/"}) == "https://x/"

    def test_websocket_url(self) -> None:
        assert _endpoint_for("websocket", {"url": "ws://x/"}) == "ws://x/"

    def test_unknown_transport_returns_empty(self) -> None:
        assert _endpoint_for("carrier-pigeon", {"url": "x"}) == ""


class TestServerFromConnection:
    def test_stdio_full(self) -> None:
        s = _server_from_connection(
            "fs",
            {"transport": "stdio", "command": "npx", "args": ["-y", "@mcp/fs"]},
        )
        assert s == McpServer(name="fs", transport="stdio", endpoint="npx -y @mcp/fs")

    def test_sse_full(self) -> None:
        s = _server_from_connection(
            "github",
            {"transport": "sse", "url": "https://mcp/github"},
        )
        assert s == McpServer(
            name="github", transport="sse", endpoint="https://mcp/github"
        )

    def test_unknown_when_not_a_dict(self) -> None:
        s = _server_from_connection("x", "not-a-dict")
        assert s == McpServer(name="x", transport="unknown", endpoint="")

    def test_missing_transport_key(self) -> None:
        s = _server_from_connection("x", {"url": "https://x"})
        assert s.transport == "unknown"


# ------------------------------------------------------------------
# Registry
# ------------------------------------------------------------------


class TestRegistry:
    def test_starts_empty(self) -> None:
        assert mcp_servers() == []

    def test_register_single(self) -> None:
        _register("fs", {"transport": "stdio", "command": "echo"})
        assert mcp_servers() == [McpServer("fs", "stdio", "echo")]

    def test_register_many(self) -> None:
        _register("a", {"transport": "stdio", "command": "x"})
        _register("b", {"transport": "sse", "url": "https://b/"})
        assert {s.name for s in mcp_servers()} == {"a", "b"}

    def test_last_write_wins_on_same_name(self) -> None:
        _register("fs", {"transport": "stdio", "command": "old"})
        _register("fs", {"transport": "sse", "url": "https://new/"})

        servers = mcp_servers()

        assert len(servers) == 1
        assert servers[0] == McpServer("fs", "sse", "https://new/")

    def test_empty_name_ignored(self) -> None:
        _register("", {"transport": "stdio", "command": "x"})
        assert mcp_servers() == []

    def test_reset_clears(self) -> None:
        _register("a", {"transport": "stdio", "command": "x"})
        _reset()
        assert mcp_servers() == []

    def test_snapshot_is_a_copy(self) -> None:
        # Mutating the returned list must not affect the registry.
        _register("a", {"transport": "stdio", "command": "x"})

        snapshot = mcp_servers()
        snapshot.clear()

        assert len(mcp_servers()) == 1


# ------------------------------------------------------------------
# Patches
# ------------------------------------------------------------------


@pytest.fixture(scope="module", autouse=True)
def _ensure_patched() -> None:  # pyright: ignore[reportUnusedFunction]
    """Apply patches once for the module; idempotent for safety."""
    _patch_mcp_adapter()
    _patch_mcp_adapter()  # second call must be a no-op


class TestMultiServerMCPClientPatch:
    def test_init_records_declared_servers(self) -> None:
        MultiServerMCPClient(
            {
                "fs": {"transport": "stdio", "command": "npx", "args": ["-y", "@fs"]},
                "gh": {"transport": "sse", "url": "https://mcp/gh"},
            },
        )

        names = {s.name for s in mcp_servers()}

        assert names == {"fs", "gh"}

    def test_init_endpoint_correct_per_transport(self) -> None:
        MultiServerMCPClient(
            {
                "fs": {
                    "transport": "stdio",
                    "command": "fs-server",
                    "args": ["--root", "/tmp"],
                },
                "api": {"transport": "streamable_http", "url": "https://mcp/api"},
            },
        )

        by_name = {s.name: s for s in mcp_servers()}

        assert by_name["fs"].endpoint == "fs-server --root /tmp"
        assert by_name["api"].endpoint == "https://mcp/api"

    def test_two_clients_aggregate_per_process(self) -> None:
        MultiServerMCPClient({"a": {"transport": "stdio", "command": "x"}})  # pyright: ignore[reportArgumentType]
        MultiServerMCPClient({"b": {"transport": "sse", "url": "https://b"}})  # pyright: ignore[reportArgumentType]

        assert {s.name for s in mcp_servers()} == {"a", "b"}

    def test_same_name_in_two_clients_last_wins(self) -> None:
        MultiServerMCPClient({"shared": {"transport": "stdio", "command": "first"}})  # pyright: ignore[reportArgumentType]
        MultiServerMCPClient(
            {"shared": {"transport": "sse", "url": "https://second"}},
        )

        servers = mcp_servers()

        assert len(servers) == 1
        assert servers[0].endpoint == "https://second"

    def test_idempotent_construction_does_not_explode(self) -> None:
        # Re-running the same config produces a single registry entry.
        cfg: dict[str, Any] = {"x": {"transport": "stdio", "command": "p"}}
        MultiServerMCPClient(cfg)
        MultiServerMCPClient(cfg)

        assert len(mcp_servers()) == 1


class TestTransportPatches:
    """The four ``mcp.client.*`` transport functions register synthesised entries.

    Tests call the patched function and immediately discard the
    returned async context manager, registration happens before the
    original is invoked, so we never actually open a transport.
    """

    def test_stdio_transport_registers_synthesised(self) -> None:
        params = StdioServerParameters(command="python", args=["-c", "pass"])
        ctx = stdio_client(params)
        del ctx  # registration already happened; drop the context manager unused

        servers = mcp_servers()
        assert any(
            s.transport == "stdio" and s.endpoint == "python -c pass" for s in servers
        )
        assert any(s.name.startswith("stdio:") for s in servers)

    def test_streamable_http_new_name_registers(self) -> None:
        ctx = streamable_http_client("https://t.example/")
        del ctx

        endpoints = [s.endpoint for s in mcp_servers()]
        assert "https://t.example/" in endpoints

    def test_streamable_http_old_name_registers(self) -> None:
        # Deprecated spelling, what langchain-mcp-adapters still uses.
        with warnings.catch_warnings():
            warnings.simplefilter("ignore", DeprecationWarning)
            ctx = streamablehttp_client("https://old.example/")  # pyright: ignore[reportDeprecated]
        del ctx

        endpoints = [s.endpoint for s in mcp_servers()]
        assert "https://old.example/" in endpoints

    def test_sse_transport_registers(self) -> None:
        ctx = sse_client("https://sse.example/")
        del ctx  # registration already happened; drop the context manager unused

        endpoints = [s.endpoint for s in mcp_servers()]
        assert "https://sse.example/" in endpoints

    def test_websocket_transport_registers(self) -> None:
        ctx = websocket_client("ws://ws.example/")
        del ctx  # registration already happened; drop the context manager unused

        endpoints = [s.endpoint for s in mcp_servers()]
        assert "ws://ws.example/" in endpoints

    def test_dedup_skips_synthesised_when_user_named_exists(self) -> None:
        # Init via MultiServerMCPClient gives a user-supplied name
        # for the same (transport, endpoint) the transport patch will
        # later see.  The transport-layer registration must not add a
        # duplicate synthesised entry.
        MultiServerMCPClient(
            {"named": {"transport": "sse", "url": "https://shared/"}},
        )

        ctx = sse_client("https://shared/")
        del ctx  # registration already happened; drop the context manager unused

        servers = mcp_servers()
        # Exactly one entry, the user-supplied name wins.
        assert len(servers) == 1
        assert servers[0].name == "named"


class TestRawMcpClientPattern:
    """Mirror raw-``mcp``-library usage that bypasses MultiServerMCPClient.

    Some agents import the raw ``mcp`` library + ``load_mcp_tools`` and
    bypass ``MultiServerMCPClient`` entirely.  The transport-layer
    patch is the only thing that catches it.
    """

    def test_streamable_http_url_captured(self) -> None:
        # async with streamable_http_client(settings.mcp_weather_url) as (...)
        ctx = streamable_http_client("http://mcp-weather:8001/mcp")
        del ctx

        servers = mcp_servers()
        match = [s for s in servers if s.endpoint == "http://mcp-weather:8001/mcp"]
        assert len(match) == 1
        assert match[0].transport == "streamable_http"


class TestRebindAcrossModules:
    """The transport rebind must reach langchain_mcp_adapters' own bindings.

    The langchain adapter's ``_create_*_session`` helpers do
    ``from mcp.client.* import *_client`` at import time, so the
    canonical ``mcp.client.X.Y_client`` and the adapter's
    ``langchain_mcp_adapters.sessions.Y_client`` are independent
    bindings.  Both must point at our wrapper after patching.
    """

    def test_adapter_sessions_bindings_are_patched(self) -> None:
        for attr in ("stdio_client", "sse_client"):
            fn = getattr(adapter_sessions, attr, None)
            assert fn is not None, f"{attr} not present in adapter sessions"
            assert getattr(fn, "_adrian_mcp_patched", False), (
                f"{attr} in langchain_mcp_adapters.sessions was not rebound"
            )
        http_names = [
            name
            for name in ("streamable_http_client", "streamablehttp_client")
            if getattr(adapter_sessions, name, None) is not None
        ]
        assert http_names, "no streamable-http client present in adapter sessions"
        for name in http_names:
            fn = getattr(adapter_sessions, name)
            assert getattr(fn, "_adrian_mcp_patched", False), (
                f"{name} in langchain_mcp_adapters.sessions was not rebound"
            )


class TestPatchIdempotency:
    def test_class_patch_flag_set(self) -> None:
        assert getattr(MultiServerMCPClient, "_adrian_mcp_patched", False) is True

    def test_re_patch_does_not_rewrap(self) -> None:
        # If patches re-wrapped, the method identity would change.
        # Reads through the canonical module so we observe whatever the
        # most recent rebind did.
        before_init = MultiServerMCPClient.__init__
        before_stdio = mcp_stdio_mod.stdio_client

        adrian_mcp._patch_mcp_adapter()

        after_stdio = mcp_stdio_mod.stdio_client

        assert MultiServerMCPClient.__init__ is before_init
        assert after_stdio is before_stdio


# ------------------------------------------------------------------
# on_mcp_server callback
# ------------------------------------------------------------------


class TestOnMcpServerCallback:
    """The ``on_mcp_server`` callback fires on genuine state changes."""

    def _init_with(self, cb: Any) -> None:  # noqa: ANN401
        """Initialise Adrian with the given on_mcp_server callback.

        Uses ``auto_instrument=False`` so the test does not also
        re-patch LangChain (the MCP patches still run unconditionally
        from ``init()``).
        """
        adrian.init(
            log_file="/tmp/test-on-mcp-server.jsonl",
            auto_instrument=False,
            on_mcp_server=cb,
        )

    def test_sync_callback_fires_on_adapter_register(self) -> None:
        seen: list[McpServer] = []

        self._init_with(seen.append)
        _reset()

        MultiServerMCPClient(
            {"named": {"transport": "sse", "url": "https://x/"}},
        )

        assert seen == [McpServer("named", "sse", "https://x/")]

    def test_sync_callback_fires_on_transport_register(self) -> None:
        seen: list[McpServer] = []

        self._init_with(seen.append)
        _reset()

        ctx = sse_client("https://transport.example/")
        del ctx

        assert len(seen) == 1
        assert seen[0].endpoint == "https://transport.example/"
        assert seen[0].transport == "sse"

    def test_no_fire_on_duplicate_observation(self) -> None:
        seen: list[McpServer] = []

        self._init_with(seen.append)
        _reset()

        # Construct same client config twice → same server → one fire.
        cfg = {"name": {"transport": "sse", "url": "https://y/"}}
        MultiServerMCPClient(cfg)  # pyright: ignore[reportArgumentType]
        MultiServerMCPClient(cfg)  # pyright: ignore[reportArgumentType]

        assert len(seen) == 1

    def test_no_fire_when_dedup_skips(self) -> None:
        seen: list[McpServer] = []

        self._init_with(seen.append)
        _reset()

        # Adapter registers user-named entry first.
        MultiServerMCPClient(
            {"named": {"transport": "sse", "url": "https://shared/"}},
        )
        # Transport open for the same endpoint → dedup skips it.
        ctx = sse_client("https://shared/")
        del ctx

        assert len(seen) == 1
        assert seen[0].name == "named"

    def test_callback_exception_does_not_break_registration(self) -> None:
        def boom(_s: McpServer) -> None:
            raise RuntimeError("intentional")

        self._init_with(boom)
        _reset()

        MultiServerMCPClient(
            {"name": {"transport": "sse", "url": "https://e/"}},
        )

        # Registration still happened despite the callback raising.
        assert len(mcp_servers()) == 1

    async def test_async_callback_runs(self) -> None:
        seen: list[McpServer] = []

        async def acb(s: McpServer) -> None:
            seen.append(s)

        self._init_with(acb)
        _reset()

        MultiServerMCPClient(
            {"a": {"transport": "sse", "url": "https://a/"}},
        )

        # Yield to the event loop so the scheduled task runs.
        await asyncio.sleep(0)

        assert len(seen) == 1
        assert seen[0].name == "a"

    def test_no_callback_configured_is_silent(self) -> None:
        # No init() call at all, _fire_on_mcp_server should silently
        # no-op via is_initialized() check.
        adrian.shutdown()  # ensure unset
        _reset()

        # Should not raise.
        MultiServerMCPClient(
            {"x": {"transport": "sse", "url": "https://x/"}},
        )

        assert len(mcp_servers()) == 1
