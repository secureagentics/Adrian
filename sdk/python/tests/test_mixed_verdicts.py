"""Tests for mixed benign/malicious tool calls from a single LLM message.

Scenario: An LLM emits multiple tool_calls in one AIMessage. Some are
classified as M0 (benign) and some as M4 (malicious). Only the malicious
ones should be blocked; benign ones must execute normally.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path
from typing import cast

import adrian
import pytest
from adrian.proto import event_pb2 as pb
from adrian.ws import WebSocketClient
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import RunnableConfig, ensure_config
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime


def _runtime_config() -> RunnableConfig:
    return ensure_config({CONF: {CONFIG_KEY_RUNTIME: Runtime()}})


def _apply_mode(
    ws: WebSocketClient,
    mode: int,
    *,
    policy_m0: bool = False,
    policy_m2: bool = False,
    policy_m3: bool = False,
    policy_m4: bool = False,
) -> pb.PolicySnapshot:
    policy = pb.PolicySnapshot(
        mode=cast("pb.Mode", mode),
        policy_m0=policy_m0,
        policy_m2=policy_m2,
        policy_m3=policy_m3,
        policy_m4=policy_m4,
    )
    ws._mode = mode
    ws._policy = policy
    ws._login_ack_received.set()
    return policy


@pytest.fixture(autouse=True)
def _cleanup() -> Iterator[None]:
    yield
    adrian.shutdown()


def _init_sdk(tmp_path: Path, block_timeout: float = 2.0) -> WebSocketClient:
    adrian.init(
        api_key="test-key",
        log_file=str(tmp_path / "events.jsonl"),
        auto_instrument=True,
        ws_url="ws://x",
        block_timeout=block_timeout,
    )
    ws = adrian._ws_client
    assert ws is not None
    return ws


class TestMixedBenignMaliciousToolCalls:
    """Single LLM message emits multiple tool_calls with different verdicts."""

    async def test_three_tools_one_blocked_two_allowed(self, tmp_path: Path) -> None:
        """LLM emits 3 tool_calls: read_file (M0), search_web (M2),
        delete_data (M4). Only delete_data should be blocked."""
        executed: dict[str, str] = {}

        def read_file(path: str) -> str:
            """Read a file."""
            executed["read_file"] = path
            return f"contents of {path}"

        def search_web(query: str) -> str:
            """Search the web."""
            executed["search_web"] = query
            return f"results for {query}"

        def delete_data(target: str) -> str:
            """Delete data — dangerous operation."""
            executed["delete_data"] = target
            return f"deleted {target}"

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()

        # Each tool_call maps to a different LLM event with a different verdict
        ws._tool_call_id_to_event_id["tc-read"] = "llm-read"
        ws._tool_call_id_to_event_id["tc-search"] = "llm-search"
        ws._tool_call_id_to_event_id["tc-delete"] = "llm-delete"

        # M0 — benign, policy_m0 is False so NOT in scope → allow
        fut_read = ws.register_pending("llm-read")
        fut_read.set_result(
            pb.Verdict(event_id="llm-read", mad_code="M0_benign", policy=policy)
        )

        # M2 — policy_m2 is False so NOT in scope → allow
        fut_search = ws.register_pending("llm-search")
        fut_search.set_result(
            pb.Verdict(event_id="llm-search", mad_code="M2_misuse", policy=policy)
        )

        # M4 — policy_m4 is True so IN scope → BLOCK
        fut_delete = ws.register_pending("llm-delete")
        fut_delete.set_result(
            pb.Verdict(event_id="llm-delete", mad_code="M4_exfiltration", policy=policy)
        )

        # Dispatch each tool independently (as ToolNode does)
        for tc_id, tool_name, tool_fn, args_key, args_val in [
            ("tc-read", "read_file", read_file, "path", "/etc/hosts"),
            ("tc-search", "search_web", search_web, "query", "python docs"),
            ("tc-delete", "delete_data", delete_data, "target", "user_data"),
        ]:
            ai = AIMessage(
                content="",
                tool_calls=[
                    {"id": tc_id, "name": tool_name, "args": {args_key: args_val}}
                ],
            )
            tn = ToolNode([tool_fn])
            await tn.ainvoke({"messages": [ai]}, config=_runtime_config())

        # Benign tools should have executed
        assert executed.get("read_file") == "/etc/hosts"
        assert executed.get("search_web") == "python docs"
        # Malicious tool should NOT have executed
        assert "delete_data" not in executed

    async def test_all_m4_all_blocked(self, tmp_path: Path) -> None:
        """All tool_calls classified as M4 — all should be blocked."""
        executed: dict[str, bool] = {}

        def tool_a(x: str) -> str:
            """Tool A."""
            executed["a"] = True
            return x

        def tool_b(x: str) -> str:
            """Tool B."""
            executed["b"] = True
            return x

        def tool_c(x: str) -> str:
            """Tool C."""
            executed["c"] = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()

        for tc_id, evt_id in [("tc-a", "llm-a"), ("tc-b", "llm-b"), ("tc-c", "llm-c")]:
            ws._tool_call_id_to_event_id[tc_id] = evt_id
            fut = ws.register_pending(evt_id)
            fut.set_result(
                pb.Verdict(event_id=evt_id, mad_code="M4_attack", policy=policy)
            )

        for tc_id, name, fn in [
            ("tc-a", "tool_a", tool_a),
            ("tc-b", "tool_b", tool_b),
            ("tc-c", "tool_c", tool_c),
        ]:
            ai = AIMessage(
                content="", tool_calls=[{"id": tc_id, "name": name, "args": {"x": "y"}}]
            )
            result = await ToolNode([fn]).ainvoke(
                {"messages": [ai]}, config=_runtime_config()
            )
            assert "BLOCKED" in result["messages"][0].content

        assert not executed

    async def test_all_benign_all_run(self, tmp_path: Path) -> None:
        """All tool_calls classified as M0 — all should run."""
        executed: dict[str, bool] = {}

        def tool_a(x: str) -> str:
            """Tool A."""
            executed["a"] = True
            return x

        def tool_b(x: str) -> str:
            """Tool B."""
            executed["b"] = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)  # only m4 blocked
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()

        for tc_id, evt_id in [("tc-a", "llm-a"), ("tc-b", "llm-b")]:
            ws._tool_call_id_to_event_id[tc_id] = evt_id
            fut = ws.register_pending(evt_id)
            fut.set_result(pb.Verdict(event_id=evt_id, mad_code="M0_ok", policy=policy))

        for tc_id, name, fn in [("tc-a", "tool_a", tool_a), ("tc-b", "tool_b", tool_b)]:
            ai = AIMessage(
                content="", tool_calls=[{"id": tc_id, "name": name, "args": {"x": "y"}}]
            )
            await ToolNode([fn]).ainvoke({"messages": [ai]}, config=_runtime_config())

        assert executed == {"a": True, "b": True}

    async def test_m2_in_scope_blocked_m0_allowed(self, tmp_path: Path) -> None:
        """policy_m2=True: M2 tools are blocked, M0 tools run."""
        executed: dict[str, bool] = {}

        def benign_tool(x: str) -> str:
            """Benign."""
            executed["benign"] = True
            return x

        def suspicious_tool(x: str) -> str:
            """Suspicious."""
            executed["suspicious"] = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m2=True, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()

        ws._tool_call_id_to_event_id["tc-benign"] = "llm-benign"
        ws._tool_call_id_to_event_id["tc-suspicious"] = "llm-suspicious"

        fut_b = ws.register_pending("llm-benign")
        fut_b.set_result(
            pb.Verdict(event_id="llm-benign", mad_code="M0_ok", policy=policy)
        )

        fut_s = ws.register_pending("llm-suspicious")
        fut_s.set_result(
            pb.Verdict(event_id="llm-suspicious", mad_code="M2_misuse", policy=policy)
        )

        # Benign runs
        ai_b = AIMessage(
            content="",
            tool_calls=[{"id": "tc-benign", "name": "benign_tool", "args": {"x": "y"}}],
        )
        await ToolNode([benign_tool]).ainvoke(
            {"messages": [ai_b]}, config=_runtime_config()
        )
        assert executed.get("benign") is True

        # Suspicious blocked
        ai_s = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-suspicious", "name": "suspicious_tool", "args": {"x": "y"}}
            ],
        )
        result = await ToolNode([suspicious_tool]).ainvoke(
            {"messages": [ai_s]}, config=_runtime_config()
        )
        assert "suspicious" not in executed
        assert "BLOCKED" in result["messages"][0].content

    async def test_sync_tools_mixed_verdicts_from_worker_thread(
        self, tmp_path: Path
    ) -> None:
        """Same as test_three_tools but with SYNC tools (worker thread path).
        This is the exact scenario from run 019eda10."""
        executed: dict[str, str] = {}

        def safe_read(path: str) -> str:
            """Safe read tool."""
            executed["safe_read"] = path
            return f"contents: {path}"

        def dangerous_write(path: str) -> str:
            """Dangerous write tool."""
            executed["dangerous_write"] = path
            return f"wrote: {path}"

        def safe_list(directory: str) -> str:
            """Safe list tool."""
            executed["safe_list"] = directory
            return f"files in {directory}"

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_BLOCK, policy_m4=True)
        ws._connected.set()
        ws._loop = asyncio.get_running_loop()

        ws._tool_call_id_to_event_id["tc-sread"] = "llm-sread"
        ws._tool_call_id_to_event_id["tc-dwrite"] = "llm-dwrite"
        ws._tool_call_id_to_event_id["tc-slist"] = "llm-slist"

        # M0 — allowed
        fut1 = ws.register_pending("llm-sread")
        fut1.set_result(
            pb.Verdict(event_id="llm-sread", mad_code="M0_ok", policy=policy)
        )
        # M4 — blocked
        fut2 = ws.register_pending("llm-dwrite")
        fut2.set_result(
            pb.Verdict(event_id="llm-dwrite", mad_code="M4_data_exfil", policy=policy)
        )
        # M0 — allowed
        fut3 = ws.register_pending("llm-slist")
        fut3.set_result(
            pb.Verdict(event_id="llm-slist", mad_code="M0_ok", policy=policy)
        )

        # safe_read (M0) → should run
        ai1 = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-sread", "name": "safe_read", "args": {"path": "/tmp/ok"}}
            ],
        )
        await ToolNode([safe_read]).ainvoke(
            {"messages": [ai1]}, config=_runtime_config()
        )

        # dangerous_write (M4) → should block
        ai2 = AIMessage(
            content="",
            tool_calls=[
                {
                    "id": "tc-dwrite",
                    "name": "dangerous_write",
                    "args": {"path": "/etc/shadow"},
                }
            ],
        )
        result2 = await ToolNode([dangerous_write]).ainvoke(
            {"messages": [ai2]}, config=_runtime_config()
        )

        # safe_list (M0) → should run
        ai3 = AIMessage(
            content="",
            tool_calls=[
                {"id": "tc-slist", "name": "safe_list", "args": {"directory": "/home"}}
            ],
        )
        await ToolNode([safe_list]).ainvoke(
            {"messages": [ai3]}, config=_runtime_config()
        )

        assert executed.get("safe_read") == "/tmp/ok", "M0 safe_read should have run"
        assert "dangerous_write" not in executed, "M4 dangerous_write should be BLOCKED"
        assert executed.get("safe_list") == "/home", "M0 safe_list should have run"
        assert "BLOCKED" in result2["messages"][0].content

    async def test_hitl_approve_benign_reject_malicious(self, tmp_path: Path) -> None:
        """HITL mode: human approves a benign tool, rejects a malicious one."""
        executed: dict[str, bool] = {}

        async def benign_tool(x: str) -> str:
            """Benign tool."""
            executed["benign"] = True
            return x

        async def malicious_tool(x: str) -> str:
            """Malicious tool."""
            executed["malicious"] = True
            return x

        ws = _init_sdk(tmp_path)
        policy = _apply_mode(ws, pb.MODE_HITL, policy_m4=True)
        ws._connected.set()

        # Benign: human approves
        ws._tool_call_id_to_event_id["tc-good"] = "llm-good"
        fut_good = ws.register_pending("llm-good")
        v_good = pb.Verdict(event_id="llm-good", mad_code="M4_a", policy=policy)
        v_good.hitl.continue_execution = True
        fut_good.set_result(v_good)

        # Malicious: human rejects
        ws._tool_call_id_to_event_id["tc-bad"] = "llm-bad"
        fut_bad = ws.register_pending("llm-bad")
        v_bad = pb.Verdict(event_id="llm-bad", mad_code="M4_a", policy=policy)
        v_bad.hitl.continue_execution = False
        fut_bad.set_result(v_bad)

        # Benign runs
        ai_good = AIMessage(
            content="",
            tool_calls=[{"id": "tc-good", "name": "benign_tool", "args": {"x": "y"}}],
        )
        await ToolNode([benign_tool]).ainvoke(
            {"messages": [ai_good]}, config=_runtime_config()
        )
        assert executed.get("benign") is True

        # Malicious blocked
        ai_bad = AIMessage(
            content="",
            tool_calls=[{"id": "tc-bad", "name": "malicious_tool", "args": {"x": "y"}}],
        )
        result = await ToolNode([malicious_tool]).ainvoke(
            {"messages": [ai_bad]}, config=_runtime_config()
        )
        assert "malicious" not in executed
        assert "BLOCKED" in result["messages"][0].content
