# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
"""Unit tests for ``_extract_tool_calls`` — the function whose missing shape
handling let block/HITL skip the verdict wait for ``create_react_agent`` agents.

Covers all three ToolNode input shapes. Shape 3 (per-tool-call dispatch) is the
one that previously returned ``[]`` and silently un-gated the tool.
"""
from __future__ import annotations

from langchain_core.messages import AIMessage, HumanMessage

from adrian import _extract_tool_calls

_TC = {"name": "read_file", "args": {"path": "/etc/shadow"}, "id": "call_1", "type": "tool_call"}


def test_shape1_state_dict_with_messages() -> None:
    """Hand-built StateGraph / ToolNode-as-node -> full {messages} state."""
    state = {"messages": [HumanMessage("hi"), AIMessage(content="", tool_calls=[_TC])]}
    assert [t["id"] for t in _extract_tool_calls(state)] == ["call_1"]


def test_shape2_bare_message_list() -> None:
    """A bare list of messages."""
    msgs = [HumanMessage("hi"), AIMessage(content="", tool_calls=[_TC])]
    assert [t["id"] for t in _extract_tool_calls(msgs)] == ["call_1"]


def test_shape3_per_tool_call_dict() -> None:
    """create_react_agent / prebuilt per-tool-call dispatch (the regression)."""
    state = {"__type": "tool_call", "tool_call": _TC, "state": {"messages": []}}
    # Pre-fix this returned [] -> no tool_call_id -> gate skipped -> tool ran.
    assert [t["id"] for t in _extract_tool_calls(state)] == ["call_1"]


def test_no_tool_calls_returns_empty() -> None:
    assert _extract_tool_calls({"messages": [HumanMessage("hi")]}) == []
    assert _extract_tool_calls({"__type": "tool_call", "tool_call": {"name": "x"}, "state": {}}) == []
