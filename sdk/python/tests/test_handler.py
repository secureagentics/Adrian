# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Tests for adrian.handler.AdrianCallbackHandler (new PairedEvent flow)."""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from adrian.config import AdrianConfig
from adrian.context import AgentContextTracker
from adrian.format.types import LlmPairData, PairedEvent, ToolPairData
from adrian.handler import AdrianCallbackHandler, extract_model_name
from adrian.hooks import HookRegistry
from adrian.pairing import EventPairBuffer
from langchain_core.messages import (
    AIMessage,
    BaseMessage,  # noqa: TC002
    HumanMessage,
    SystemMessage,
)
from langchain_core.outputs import ChatGeneration, LLMResult


class _Collector:
    """Tiny EventHandler that appends paired events into a list."""

    def __init__(self) -> None:
        self.events: list[PairedEvent] = []

    async def on_paired_event(self, event: PairedEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


@pytest.fixture()  # pyright: ignore[reportUntypedFunctionDecorator]
def handler_and_events() -> tuple[AdrianCallbackHandler, list[PairedEvent]]:
    """Fresh handler wired to an in-memory collector."""
    collector = _Collector()
    hooks = HookRegistry()
    hooks.register(collector)
    handler = AdrianCallbackHandler(
        pair_buffer=EventPairBuffer(),
        context_tracker=AgentContextTracker(),
        hooks=hooks,
        config=AdrianConfig(),
    )

    return handler, collector.events


class TestLlmPair:
    """A chat_model_start + llm_end pair emits exactly one LLM PairedEvent."""

    async def test_chat_model_start_buffers_no_event(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        batch: list[BaseMessage] = [
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
        ]

        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-4o"}},
            messages=[batch],
            run_id=uuid4(),
        )

        assert events == []  # pair incomplete until llm_end

    async def test_full_pair_emits_llm_event(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        run_id = uuid4()
        batch: list[BaseMessage] = [
            SystemMessage(content="sys"),
            HumanMessage(content="hello"),
        ]

        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-4o"}},
            messages=[batch],
            run_id=run_id,
        )
        gen = ChatGeneration(message=AIMessage(content="Hi there"))
        await handler.on_llm_end(response=LLMResult(generations=[[gen]]), run_id=run_id)

        assert len(events) == 1
        event = events[0]
        assert event.pair_type == "llm"
        assert event.run_id == str(run_id)
        assert isinstance(event.data, LlmPairData)
        assert event.data.output == "Hi there"
        assert event.agent.system_prompt == "sys"

    async def test_pair_carries_parent_run_id(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        run_id = uuid4()
        parent = uuid4()

        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-4o"}},
            messages=[[HumanMessage(content="hi")]],
            run_id=run_id,
            parent_run_id=parent,
        )
        gen = ChatGeneration(message=AIMessage(content="ok"))
        await handler.on_llm_end(response=LLMResult(generations=[[gen]]), run_id=run_id)

        assert events[0].parent_run_id == str(parent)


class TestToolPair:
    """tool_start + tool_end pair emits exactly one Tool PairedEvent."""

    async def test_full_tool_pair(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        run_id = uuid4()
        parent = uuid4()

        await handler.on_tool_start(
            serialized={"name": "get_grid_state"},
            input_str='{"node_id": "SOL-01"}',
            run_id=run_id,
            parent_run_id=parent,
            tool_call_id="call_abc",
        )
        await handler.on_tool_end(output='{"status": "ok"}', run_id=run_id)

        assert len(events) == 1
        event = events[0]
        assert event.pair_type == "tool"
        assert isinstance(event.data, ToolPairData)
        assert event.data.tool_name == "get_grid_state"
        assert event.data.tool_call_id == "call_abc"
        assert event.data.output == '{"status": "ok"}'
        assert event.parent_run_id == str(parent)


class TestLlmTokenUsage:
    async def test_usage_flows_into_pair(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        run_id = uuid4()

        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-4o"}},
            messages=[[HumanMessage(content="x")]],
            run_id=run_id,
        )
        gen = ChatGeneration(message=AIMessage(content="ok"))
        result = LLMResult(
            generations=[[gen]],
            llm_output={
                "token_usage": {
                    "prompt_tokens": 100,
                    "completion_tokens": 20,
                    "total_tokens": 120,
                },
            },
        )
        await handler.on_llm_end(response=result, run_id=run_id)

        assert len(events) == 1
        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.usage is not None
        assert data.usage["total_tokens"] == 120


class TestToolCallsInLlmPair:
    async def test_tool_calls_extracted(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        run_id = uuid4()

        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-4o"}},
            messages=[[HumanMessage(content="x")]],
            run_id=run_id,
        )
        msg = AIMessage(
            content="",
            tool_calls=[{"id": "call_1", "name": "get_fleet_status", "args": {}}],
        )
        await handler.on_llm_end(
            response=LLMResult(generations=[[ChatGeneration(message=msg)]]),
            run_id=run_id,
        )

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert len(data.tool_calls) == 1
        assert data.tool_calls[0]["name"] == "get_fleet_status"


class TestReasoningInLlmPair:
    """Reasoning blocks are captured alongside, not instead of, output.

    The shapes here are the two the providers actually emit: OpenAI's
    ``reasoning`` block with a summary list, and Anthropic's ``thinking``
    block, both reaching us through LangChain as content blocks.
    """

    async def _emit(
        self,
        handler: AdrianCallbackHandler,
        message: AIMessage,
    ) -> None:
        run_id = uuid4()
        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-5"}},
            messages=[[HumanMessage(content="17 * 23?")]],
            run_id=run_id,
        )
        await handler.on_llm_end(
            response=LLMResult(generations=[[ChatGeneration(message=message)]]),
            run_id=run_id,
        )

    async def test_openai_reasoning_summary_captured(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        msg = AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "First step."},
                        {"type": "summary_text", "text": "Second step."},
                    ],
                },
                {"type": "text", "text": "391"},
            ]
        )
        await self._emit(handler, msg)

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == "First step.\n\nSecond step."
        assert data.output == "391"

    async def test_anthropic_thinking_captured(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        msg = AIMessage(
            content=[
                {"type": "thinking", "thinking": "17 * 20 + 17 * 3."},
                {"type": "text", "text": "391"},
            ]
        )
        await self._emit(handler, msg)

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == "17 * 20 + 17 * 3."
        assert data.output == "391"

    async def test_plain_string_content_has_no_reasoning(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events
        await self._emit(handler, AIMessage(content="391"))

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == ""
        assert data.output == "391"

    async def test_output_version_v1_reasoning_string_blocks(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        # output_version="v1" drops the summary list and emits one block
        # per reasoning pass, each carrying a bare `reasoning` string.
        handler, events = handler_and_events
        msg = AIMessage(
            content=[
                {"type": "reasoning", "id": "rs_1", "reasoning": "First pass."},
                {"type": "reasoning", "id": "rs_1", "reasoning": "Second pass."},
                {"type": "text", "text": "391"},
            ]
        )
        await self._emit(handler, msg)

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == "First pass.\n\nSecond pass."
        assert data.output == "391"

    async def test_output_version_v0_reasoning_in_additional_kwargs(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        # output_version="v0" keeps content text-only and parks the whole
        # reasoning block in additional_kwargs.
        handler, events = handler_and_events
        msg = AIMessage(
            content=[{"type": "text", "text": "391"}],
            additional_kwargs={
                "reasoning": {
                    "id": "rs_1",
                    "type": "reasoning",
                    "summary": [
                        {"type": "summary_text", "text": "First step."},
                        {"type": "summary_text", "text": "Second step."},
                    ],
                    "encrypted_content": "gAAAA...",
                }
            },
        )
        await self._emit(handler, msg)

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == "First step.\n\nSecond step."
        assert data.output == "391"

    async def test_content_blocks_win_over_additional_kwargs(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        # The fallback must not append to, or duplicate, a block that
        # content already supplied.
        handler, events = handler_and_events
        msg = AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "summary": [{"type": "summary_text", "text": "From content."}],
                },
                {"type": "text", "text": "391"},
            ],
            additional_kwargs={"reasoning": {"reasoning": "From kwargs."}},
        )
        await self._emit(handler, msg)

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == "From content."

    async def test_encrypted_only_reasoning_yields_nothing(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        # o4-mini on an unverified org: block present, summary empty, the
        # real chain of thought only as an opaque blob.
        handler, events = handler_and_events
        msg = AIMessage(
            content=[
                {
                    "type": "reasoning",
                    "summary": [],
                    "content": [],
                    "encrypted_content": "gAAAAABqgu5W...",
                },
                {"type": "text", "text": "391"},
            ]
        )
        await self._emit(handler, msg)

        data = events[0].data
        assert isinstance(data, LlmPairData)
        assert data.reasoning == ""
        assert data.output == "391"


class TestExtractModelName:
    def test_none_returns_unknown(self) -> None:
        assert extract_model_name(None) == "unknown"

    def test_name_key(self) -> None:
        assert extract_model_name({"name": "gpt-4o"}) == "gpt-4o"

    def test_id_list(self) -> None:
        assert (
            extract_model_name({"id": ["langchain", "openai", "ChatOpenAI"]})
            == "ChatOpenAI"
        )

    def test_kwargs_model_name(self) -> None:
        result: Any = extract_model_name(
            {"kwargs": {"model_name": "claude-sonnet-4-20250514"}}
        )
        assert result == "claude-sonnet-4-20250514"

    def test_empty_dict(self) -> None:
        assert extract_model_name({}) == "unknown"
