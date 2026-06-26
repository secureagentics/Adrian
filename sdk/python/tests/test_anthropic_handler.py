# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics
#
# Licensed under the Apache Licence, Version 2.0 (the "Licence").
# You may not use this file except in compliance with the Licence.
# A copy of the Licence is included at LICENSE in the repository root.
"""Tests for the Anthropic SDK instrumentation (adrian.anthropic_handler)."""

from __future__ import annotations

from typing import Any
from unittest.mock import MagicMock

import adrian.anthropic_handler as _ah
import pytest
from adrian.anthropic_handler import (
    _derive_agent_id,
    _emit_pair,
    _extract_anthropic_tool_calls,
    _extract_anthropic_usage,
    _extract_response_text,
    _flatten_anthropic_messages,
    _flatten_content,
    anthropic_invocation,
    anthropic_invocation_sync,
    build_anthropic_llm_pair,
    patch_anthropic,
)
from adrian.config import AdrianConfig
from adrian.context import get_invocation_id, set_invocation_id
from adrian.format.types import LlmPairData, PairedEvent
from adrian.hooks import HookRegistry
from adrian.types import ChatMessage

# ------------------------------------------------------------------
# Shared helpers
# ------------------------------------------------------------------


class _Collector:
    """Minimal EventHandler that accumulates paired events."""

    def __init__(self) -> None:
        self.events: list[PairedEvent] = []

    async def on_paired_event(self, event: PairedEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


def _make_text_response(
    *,
    model: str = "claude-opus-4-6",
    text: str = "Hello!",
    input_tokens: int = 10,
    output_tokens: int = 5,
) -> MagicMock:
    """Build a minimal mock Anthropic Message response."""
    text_block = MagicMock()
    text_block.type = "text"
    text_block.text = text

    usage = MagicMock()
    usage.input_tokens = input_tokens
    usage.output_tokens = output_tokens

    response = MagicMock()
    response.model = model
    response.content = [text_block]
    response.usage = usage

    return response


def _wired_hooks(config: AdrianConfig) -> tuple[HookRegistry, _Collector]:
    """Return a HookRegistry + Collector pair and wire them into the handler."""
    collector = _Collector()
    hooks = HookRegistry()
    hooks.register(collector)
    _ah._hooks_getter = lambda: hooks
    _ah._config_getter = lambda: config
    return hooks, collector


# ------------------------------------------------------------------
# _flatten_content
# ------------------------------------------------------------------


class TestFlattenContent:
    def test_plain_string_passthrough(self) -> None:
        assert _flatten_content("hello world") == "hello world"

    def test_non_list_non_str_coerced(self) -> None:
        assert _flatten_content(42) == "42"  # type: ignore[arg-type]

    def test_text_block_dict(self) -> None:
        result = _flatten_content([{"type": "text", "text": "hi"}])
        assert result == "hi"

    def test_tool_use_dict(self) -> None:
        blocks = [{"type": "tool_use", "name": "search", "input": {"q": "test"}}]
        result = _flatten_content(blocks)
        assert "tool_use: search" in result

    def test_tool_result_string_dict(self) -> None:
        blocks = [{"type": "tool_result", "content": "42"}]
        assert _flatten_content(blocks) == "42"

    def test_sdk_text_object(self) -> None:
        block = MagicMock()
        block.type = "text"
        block.text = "SDK text"
        assert _flatten_content([block]) == "SDK text"

    def test_sdk_tool_use_object(self) -> None:
        block = MagicMock()
        block.type = "tool_use"
        block.name = "my_tool"
        block.input = {"x": 1}
        result = _flatten_content([block])
        assert "tool_use: my_tool" in result

    def test_sdk_tool_result_delegates_recursively(self) -> None:
        inner = MagicMock()
        inner.type = "text"
        inner.text = "inner text"
        outer = MagicMock()
        outer.type = "tool_result"
        outer.content = [inner]
        result = _flatten_content([outer])
        assert "inner text" in result

    def test_mixed_blocks_joined_by_newline(self) -> None:
        blocks = [
            {"type": "text", "text": "first"},
            {"type": "text", "text": "second"},
        ]
        result = _flatten_content(blocks)
        assert result == "first\nsecond"

    def test_empty_list(self) -> None:
        assert _flatten_content([]) == ""


# ------------------------------------------------------------------
# _flatten_anthropic_messages
# ------------------------------------------------------------------


class TestFlattenAnthropicMessages:
    def test_no_system(self) -> None:
        msgs = [{"role": "user", "content": "hi"}]
        result = _flatten_anthropic_messages(msgs, None)
        assert len(result) == 1
        assert result[0]["role"] == "user"
        assert result[0]["content"] == "hi"

    def test_system_prepended_as_first_entry(self) -> None:
        msgs = [{"role": "user", "content": "hello"}]
        result = _flatten_anthropic_messages(msgs, "You are helpful.")
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "You are helpful."
        assert result[1]["role"] == "user"

    def test_system_as_block_list(self) -> None:
        system = [{"type": "text", "text": "block system"}]
        result = _flatten_anthropic_messages([], system)
        assert result[0]["role"] == "system"
        assert result[0]["content"] == "block system"

    def test_assistant_role_preserved(self) -> None:
        msgs = [
            {"role": "user", "content": "q"},
            {"role": "assistant", "content": "a"},
        ]
        result = _flatten_anthropic_messages(msgs, None)
        assert result[-1]["role"] == "assistant"

    def test_multi_turn_order(self) -> None:
        msgs = [
            {"role": "user", "content": "first"},
            {"role": "assistant", "content": "second"},
            {"role": "user", "content": "third"},
        ]
        result = _flatten_anthropic_messages(msgs, "sys")
        assert len(result) == 4
        assert result[0]["role"] == "system"
        assert result[1]["content"] == "first"
        assert result[3]["content"] == "third"

    def test_empty_messages_with_system(self) -> None:
        result = _flatten_anthropic_messages([], "only system")
        assert len(result) == 1
        assert result[0]["role"] == "system"


# ------------------------------------------------------------------
# _extract_anthropic_tool_calls
# ------------------------------------------------------------------


class TestExtractAnthropicToolCalls:
    def test_empty_content(self) -> None:
        assert _extract_anthropic_tool_calls([]) == []

    def test_text_block_ignored(self) -> None:
        block = MagicMock()
        block.type = "text"
        block.text = "hello"
        assert _extract_anthropic_tool_calls([block]) == []

    def test_sdk_tool_use_object(self) -> None:
        block = MagicMock()
        block.type = "tool_use"
        block.id = "call_abc"
        block.name = "get_weather"
        block.input = {"city": "London"}
        result = _extract_anthropic_tool_calls([block])
        assert len(result) == 1
        assert result[0]["id"] == "call_abc"
        assert result[0]["name"] == "get_weather"
        assert result[0]["args"] == {"city": "London"}

    def test_dict_tool_use(self) -> None:
        block = {"type": "tool_use", "id": "c1", "name": "search", "input": {"q": "x"}}
        result = _extract_anthropic_tool_calls([block])
        assert len(result) == 1
        assert result[0]["name"] == "search"
        assert result[0]["id"] == "c1"

    def test_multiple_tool_calls(self) -> None:
        def _make(name: str, id_: str) -> MagicMock:
            b = MagicMock()
            b.type = "tool_use"
            b.id = id_
            b.name = name
            b.input = {}
            return b

        result = _extract_anthropic_tool_calls(
            [_make("tool_a", "c1"), _make("tool_b", "c2")]
        )
        assert len(result) == 2
        assert {r["name"] for r in result} == {"tool_a", "tool_b"}

    def test_non_dict_input_coerced(self) -> None:
        block = MagicMock()
        block.type = "tool_use"
        block.id = "c1"
        block.name = "t"
        block.input = [("key", "val")]
        result = _extract_anthropic_tool_calls([block])
        assert isinstance(result[0]["args"], dict)


# ------------------------------------------------------------------
# _extract_anthropic_usage
# ------------------------------------------------------------------


class TestExtractAnthropicUsage:
    def test_none_when_usage_attribute_missing(self) -> None:
        assert _extract_anthropic_usage(object()) is None

    def test_none_when_usage_is_none(self) -> None:
        response = MagicMock()
        response.usage = None
        assert _extract_anthropic_usage(response) is None

    def test_extracts_tokens_correctly(self) -> None:
        usage = MagicMock()
        usage.input_tokens = 150
        usage.output_tokens = 30
        response = MagicMock()
        response.usage = usage
        result = _extract_anthropic_usage(response)
        assert result is not None
        assert result["prompt_tokens"] == 150
        assert result["completion_tokens"] == 30
        assert result["total_tokens"] == 180

    def test_zero_tokens_handled(self) -> None:
        usage = MagicMock()
        usage.input_tokens = 0
        usage.output_tokens = 0
        response = MagicMock()
        response.usage = usage
        result = _extract_anthropic_usage(response)
        assert result is not None
        assert result["total_tokens"] == 0


# ------------------------------------------------------------------
# _extract_response_text
# ------------------------------------------------------------------


class TestExtractResponseText:
    def test_single_text_block(self) -> None:
        block = MagicMock()
        block.type = "text"
        block.text = "The answer."
        assert _extract_response_text([block]) == "The answer."

    def test_multiple_text_blocks_joined(self) -> None:
        def _tb(text: str) -> MagicMock:
            b = MagicMock()
            b.type = "text"
            b.text = text
            return b

        result = _extract_response_text([_tb("line1"), _tb("line2")])
        assert result == "line1\nline2"

    def test_non_text_blocks_skipped(self) -> None:
        tool = MagicMock()
        tool.type = "tool_use"
        text = MagicMock()
        text.type = "text"
        text.text = "answer"
        assert _extract_response_text([tool, text]) == "answer"

    def test_empty_content(self) -> None:
        assert _extract_response_text([]) == ""

    def test_dict_text_block(self) -> None:
        assert (
            _extract_response_text([{"type": "text", "text": "dict text"}])
            == "dict text"
        )


# ------------------------------------------------------------------
# _derive_agent_id
# ------------------------------------------------------------------


class TestDeriveAgentId:
    def test_default_when_no_system(self) -> None:
        msgs: list[ChatMessage] = [ChatMessage(role="user", content="hi")]
        assert _derive_agent_id(msgs) == "default"

    def test_uses_system_prompt(self) -> None:
        msgs: list[ChatMessage] = [
            ChatMessage(role="system", content="You are a code assistant."),
            ChatMessage(role="user", content="Help me."),
        ]
        assert _derive_agent_id(msgs) == "You are a code assistant."

    def test_truncates_at_64_chars(self) -> None:
        msgs: list[ChatMessage] = [ChatMessage(role="system", content="x" * 100)]
        assert len(_derive_agent_id(msgs)) == 64

    def test_newlines_replaced_with_spaces(self) -> None:
        msgs: list[ChatMessage] = [ChatMessage(role="system", content="line1\nline2")]
        result = _derive_agent_id(msgs)
        assert "\n" not in result

    def test_empty_system_prompt_falls_back(self) -> None:
        msgs: list[ChatMessage] = [
            ChatMessage(role="system", content="   "),
            ChatMessage(role="user", content="hi"),
        ]
        assert _derive_agent_id(msgs) == "default"


# ------------------------------------------------------------------
# build_anthropic_llm_pair
# ------------------------------------------------------------------


class TestBuildAnthropicLlmPair:
    def test_pair_type_is_llm(self) -> None:
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert pair.pair_type == "llm"

    def test_ids_propagated(self) -> None:
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(),
            model="m",
            session_id="sess-abc",
            invocation_id="inv-xyz",
            run_id="run-1",
        )
        assert pair.session_id == "sess-abc"
        assert pair.invocation_id == "inv-xyz"
        assert pair.run_id == "run-1"

    def test_model_from_response_preferred_over_request(self) -> None:
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(model="claude-haiku-4-5"),
            model="request-model",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert isinstance(pair.data, LlmPairData)
        assert pair.data.model == "claude-haiku-4-5"

    def test_fallback_to_request_model_when_response_empty(self) -> None:
        resp = _make_text_response()
        resp.model = ""
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=resp,
            model="fallback-model",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert isinstance(pair.data, LlmPairData)
        assert pair.data.model == "fallback-model"

    def test_system_prompt_extracted(self) -> None:
        flat_msgs: list[ChatMessage] = [
            ChatMessage(role="system", content="You are a triage agent."),
            ChatMessage(role="user", content="Help."),
        ]
        pair = build_anthropic_llm_pair(
            flat_messages=flat_msgs,
            response=_make_text_response(),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert pair.agent.system_prompt == "You are a triage agent."

    def test_last_user_message_is_user_instruction(self) -> None:
        flat_msgs: list[ChatMessage] = [
            ChatMessage(role="system", content="sys"),
            ChatMessage(role="user", content="first question"),
            ChatMessage(role="assistant", content="answer"),
            ChatMessage(role="user", content="follow-up"),
        ]
        pair = build_anthropic_llm_pair(
            flat_messages=flat_msgs,
            response=_make_text_response(),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert pair.agent.user_instruction == "follow-up"

    def test_output_text_captured(self) -> None:
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(text="The answer is 42."),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert isinstance(pair.data, LlmPairData)
        assert pair.data.output == "The answer is 42."

    def test_token_usage_populated(self) -> None:
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(input_tokens=200, output_tokens=50),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert isinstance(pair.data, LlmPairData)
        assert pair.data.usage is not None
        assert pair.data.usage["prompt_tokens"] == 200
        assert pair.data.usage["completion_tokens"] == 50
        assert pair.data.usage["total_tokens"] == 250

    def test_tool_calls_in_data(self) -> None:
        tool_block = MagicMock()
        tool_block.type = "tool_use"
        tool_block.id = "c1"
        tool_block.name = "search"
        tool_block.input = {"query": "test"}

        usage = MagicMock()
        usage.input_tokens = 10
        usage.output_tokens = 5

        resp = MagicMock()
        resp.model = "claude-opus-4-6"
        resp.content = [tool_block]
        resp.usage = usage

        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="find it")],
            response=resp,
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert isinstance(pair.data, LlmPairData)
        assert len(pair.data.tool_calls) == 1
        assert pair.data.tool_calls[0]["name"] == "search"

    def test_event_id_is_unique_per_call(self) -> None:
        kwargs: Any = dict(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert (
            build_anthropic_llm_pair(**kwargs).event_id
            != build_anthropic_llm_pair(**kwargs).event_id
        )

    def test_parent_is_none(self) -> None:
        pair = build_anthropic_llm_pair(
            flat_messages=[ChatMessage(role="user", content="hi")],
            response=_make_text_response(),
            model="m",
            session_id="s",
            invocation_id="i",
            run_id="r",
        )
        assert pair.parent is None


# ------------------------------------------------------------------
# _emit_pair
# ------------------------------------------------------------------


class TestEmitPair:
    async def test_emits_event_to_hooks(self) -> None:
        config = AdrianConfig(session_id="sess-emit")
        _, collector = _wired_hooks(config)

        await _emit_pair(
            _make_text_response(text="Reply"),
            {
                "model": "claude-opus-4-6",
                "messages": [{"role": "user", "content": "Question"}],
                "system": "You are helpful.",
            },
        )

        assert len(collector.events) == 1
        event = collector.events[0]
        assert event.pair_type == "llm"
        assert event.session_id == "sess-emit"
        assert isinstance(event.data, LlmPairData)
        assert event.data.output == "Reply"
        assert event.agent.system_prompt == "You are helpful."

    async def test_skips_silently_when_hooks_none(self) -> None:
        _ah._hooks_getter = lambda: None
        _ah._config_getter = lambda: None
        await _emit_pair(_make_text_response(), {"model": "m", "messages": []})

    async def test_skips_silently_when_getters_not_set(self) -> None:
        _ah._hooks_getter = None
        _ah._config_getter = None
        await _emit_pair(_make_text_response(), {"model": "m", "messages": []})

    async def test_fires_on_event_callback(self) -> None:
        fired: list[str] = []

        def on_event(
            event_type: str,
            data: Any,
            run_id: str,
            parent_run_id: str | None,
            event_id: str | None,
        ) -> None:
            fired.append(event_type)

        config = AdrianConfig(session_id="s", on_event=on_event)
        _wired_hooks(config)

        await _emit_pair(
            _make_text_response(),
            {"model": "m", "messages": [{"role": "user", "content": "q"}]},
        )

        assert fired == ["llm"]

    async def test_uses_invocation_id_from_context(self) -> None:
        config = AdrianConfig(session_id="s")
        _, collector = _wired_hooks(config)

        token = set_invocation_id("fixed-inv-id")

        try:
            await _emit_pair(
                _make_text_response(),
                {"model": "m", "messages": [{"role": "user", "content": "q"}]},
            )
        finally:
            token.var.reset(token)

        assert collector.events[0].invocation_id == "fixed-inv-id"

    async def test_defaults_to_no_invocation_outside_context(self) -> None:
        config = AdrianConfig(session_id="s")
        _, collector = _wired_hooks(config)

        await _emit_pair(
            _make_text_response(),
            {"model": "m", "messages": [{"role": "user", "content": "q"}]},
        )

        assert collector.events[0].invocation_id == "no_invocation"

    async def test_token_usage_in_emitted_event(self) -> None:
        config = AdrianConfig(session_id="s")
        _, collector = _wired_hooks(config)

        await _emit_pair(
            _make_text_response(input_tokens=100, output_tokens=40),
            {"model": "m", "messages": [{"role": "user", "content": "q"}]},
        )

        data = collector.events[0].data
        assert isinstance(data, LlmPairData)
        assert data.usage is not None
        assert data.usage["total_tokens"] == 140


# ------------------------------------------------------------------
# patch_anthropic
# ------------------------------------------------------------------


class TestPatchAnthropicGetters:
    def test_getters_updated_on_each_call(self) -> None:
        hooks_a: list[HookRegistry] = [HookRegistry()]
        config_a: list[AdrianConfig] = [AdrianConfig()]

        patch_anthropic(
            hooks_getter=lambda: hooks_a[0], config_getter=lambda: config_a[0]
        )

        assert _ah._hooks_getter is not None
        assert _ah._config_getter is not None
        assert _ah._hooks_getter() is hooks_a[0]
        assert _ah._config_getter() is config_a[0]

        hooks_b = HookRegistry()
        config_b = AdrianConfig()

        patch_anthropic(hooks_getter=lambda: hooks_b, config_getter=lambda: config_b)

        assert _ah._hooks_getter() is hooks_b
        assert _ah._config_getter() is config_b

    def test_no_op_when_anthropic_not_installed(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        import sys

        saved = sys.modules.pop("anthropic", None)
        monkeypatch.setitem(sys.modules, "anthropic", None)  # type: ignore[arg-type]

        try:
            patch_anthropic(hooks_getter=lambda: None, config_getter=lambda: None)
        finally:
            if saved is not None:
                sys.modules["anthropic"] = saved
            else:
                sys.modules.pop("anthropic", None)


# ------------------------------------------------------------------
# anthropic_invocation / anthropic_invocation_sync
# ------------------------------------------------------------------


class TestAnthropicInvocationContext:
    async def test_async_sets_invocation_id(self) -> None:
        assert get_invocation_id() is None

        async with anthropic_invocation():
            inv_id = get_invocation_id()
            assert inv_id is not None
            assert len(inv_id) > 0

        assert get_invocation_id() is None

    async def test_async_id_is_uuid_format(self) -> None:
        import re

        uuid_re = re.compile(
            r"^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$"
        )

        async with anthropic_invocation():
            assert uuid_re.match(get_invocation_id() or "") is not None

    async def test_async_resets_on_exit(self) -> None:
        outer_token = set_invocation_id("outer")

        async with anthropic_invocation():
            inner_id = get_invocation_id()
            assert inner_id != "outer"

        assert get_invocation_id() == "outer"
        outer_token.var.reset(outer_token)

    def test_sync_sets_invocation_id(self) -> None:
        assert get_invocation_id() is None

        with anthropic_invocation_sync():
            inv_id = get_invocation_id()
            assert inv_id is not None

        assert get_invocation_id() is None

    async def test_two_consecutive_invocations_have_different_ids(self) -> None:
        ids: list[str] = []

        async with anthropic_invocation():
            ids.append(get_invocation_id() or "")

        async with anthropic_invocation():
            ids.append(get_invocation_id() or "")

        assert ids[0] != ids[1]
