# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Integration tests for always-on PII redaction in adrian.init()."""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from collections.abc import Iterator
from pathlib import Path
from uuid import uuid4

import adrian
import pytest
from adrian.format.types import LlmPairData, PairedEvent
from adrian.pii import RedactingHandler
from langchain_core.messages import AIMessage, BaseMessage, HumanMessage, SystemMessage
from langchain_core.outputs import ChatGeneration, LLMResult


class _Collector:
    """EventHandler that collects paired events in a list."""

    def __init__(self) -> None:
        self.events: list[PairedEvent] = []

    async def on_paired_event(self, event: PairedEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


@pytest.fixture(autouse=True)  # pyright: ignore[reportUntypedFunctionDecorator]
def _cleanup() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    yield
    adrian.shutdown()


class TestPiiRedactionAlwaysOn:
    def test_handlers_always_wrapped(self, tmp_path: Path) -> None:
        """Every handler is wrapped in RedactingHandler automatically."""
        collector = _Collector()
        adrian.init(
            log_file=str(tmp_path / "events.jsonl"),
            handlers=[collector],
            auto_instrument=False,
        )

        hooks = adrian._hooks
        assert hooks is not None
        assert len(hooks._handlers) == 1
        assert isinstance(hooks._handlers[0], RedactingHandler)

    async def test_events_redacted(self, tmp_path: Path) -> None:
        """Events emitted through init() have PII stripped."""
        collector = _Collector()
        adrian.init(
            log_file=str(tmp_path / "events.jsonl"),
            handlers=[collector],
            auto_instrument=False,
        )

        handler = adrian._handler
        assert handler is not None

        run_id = uuid4()
        batch: list[BaseMessage] = [
            SystemMessage(content="You are helpful"),
            HumanMessage(content="My email is user@secret.com"),
        ]

        await handler.on_chat_model_start(
            serialized={"kwargs": {"model_name": "gpt-4o"}},
            messages=[batch],
            run_id=run_id,
        )
        gen = ChatGeneration(
            message=AIMessage(content="Got it, contacting user@secret.com")
        )
        await handler.on_llm_end(
            response=LLMResult(generations=[[gen]]),
            run_id=run_id,
        )

        assert len(collector.events) == 1
        event = collector.events[0]
        assert isinstance(event.data, LlmPairData)
        assert "user@secret.com" not in event.data.output
        assert "[EMAIL_REDACTED]" in event.data.output

        for msg in event.data.messages:
            assert "user@secret.com" not in msg["content"]
