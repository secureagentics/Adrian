"""Tests for adrian.pii._redactor, PairedEvent redaction."""

from __future__ import annotations

from adrian.format.types import (
    AgentContext,
    LlmPairData,
    PairedEvent,
    ParentContext,
    ToolPairData,
)
from adrian.pii._engine import PiiConfig
from adrian.pii._redactor import PiiRedactor, RedactingHandler
from adrian.pii._strategies import RedactionStrategy


def _llm_event(
    messages: list[dict[str, str]] | None = None,
    output: str = "",
    tool_calls: list[dict[str, object]] | None = None,
    system_prompt: str = "",
    user_instruction: str = "",
    parent: ParentContext | None = None,
) -> PairedEvent:
    return PairedEvent(
        event_id="evt-1",
        invocation_id="inv-1",
        session_id="sess-1",
        run_id="run-1",
        timestamp="2026-01-01T00:00:00Z",
        pair_type="llm",
        agent=AgentContext(
            agent_id="agent",
            system_prompt=system_prompt,
            user_instruction=user_instruction,
        ),
        parent=parent,
        data=LlmPairData(
            model="ChatAnthropic",
            messages=messages or [],  # type: ignore[arg-type]
            output=output,
            tool_calls=tool_calls or [],  # type: ignore[arg-type]
        ),
    )


def _tool_event(
    input_str: str = "",
    output_str: str = "",
    system_prompt: str = "",
) -> PairedEvent:
    return PairedEvent(
        event_id="evt-2",
        invocation_id="inv-1",
        session_id="sess-1",
        run_id="run-2",
        timestamp="2026-01-01T00:00:00Z",
        pair_type="tool",
        agent=AgentContext(agent_id="agent", system_prompt=system_prompt),
        parent=None,
        data=ToolPairData(
            tool_name="test_tool",
            input=input_str,
            output=output_str,
        ),
    )


class TestPiiRedactorLlm:
    def test_redacts_message_content(self) -> None:
        event = _llm_event(
            messages=[{"role": "user", "content": "my email is user@test.com"}],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert "user@test.com" not in redacted.data.messages[0]["content"]  # type: ignore[union-attr]
        assert "[EMAIL_REDACTED]" in redacted.data.messages[0]["content"]  # type: ignore[union-attr]

    def test_redacts_output(self) -> None:
        event = _llm_event(output="call 555-123-4567")
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        assert "555-123-4567" not in redacted.data.output
        assert "[PHONE_REDACTED]" in redacted.data.output

    def test_redacts_tool_call_args(self) -> None:
        event = _llm_event(
            tool_calls=[
                {"id": "tc-1", "name": "send", "args": {"to": "user@test.com"}}
            ],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        args = redacted.data.tool_calls[0]["args"]
        assert "user@test.com" not in str(args)

    def test_redacts_system_prompt(self) -> None:
        event = _llm_event(system_prompt="Contact admin@corp.com for help")
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert "admin@corp.com" not in redacted.agent.system_prompt

    def test_redacts_user_instruction(self) -> None:
        event = _llm_event(user_instruction="My SSN is 123-45-6789")
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert "123-45-6789" not in redacted.agent.user_instruction


class TestPiiRedactorNestedArgs:
    """Regression tests for the previously-skipped nested arg paths."""

    def test_nested_list_of_emails(self) -> None:
        event = _llm_event(
            tool_calls=[
                {
                    "id": "tc-1",
                    "name": "send_bulk",
                    "args": {"recipients": ["a@x.com", "b@y.com"]},
                },
            ],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        s = str(redacted.data.tool_calls[0]["args"])
        assert "a@x.com" not in s
        assert "b@y.com" not in s
        assert s.count("[EMAIL_REDACTED]") == 2

    def test_nested_dict(self) -> None:
        event = _llm_event(
            tool_calls=[
                {
                    "id": "tc-1",
                    "name": "create_user",
                    "args": {
                        "profile": {"email": "deep@x.com", "phone": "555-123-4567"}
                    },
                },
            ],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        s = str(redacted.data.tool_calls[0]["args"])
        assert "deep@x.com" not in s
        assert "555-123-4567" not in s

    def test_mixed_nesting(self) -> None:
        event = _llm_event(
            tool_calls=[
                {
                    "id": "tc-1",
                    "name": "f",
                    "args": {
                        "users": [
                            {"email": "a@x.com", "ssn": "123-45-6789"},
                            {"email": "b@y.com"},
                        ],
                        "count": 2,
                        "verified": True,
                    },
                },
            ],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        s = str(redacted.data.tool_calls[0]["args"])
        assert "a@x.com" not in s
        assert "b@y.com" not in s
        assert "123-45-6789" not in s

    def test_frozenset_recursed(self) -> None:
        event = _llm_event(
            tool_calls=[
                {
                    "id": "tc-1",
                    "name": "f",
                    "args": {"tags": frozenset({"a@x.com", "label"})},
                },
            ],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        tags = redacted.data.tool_calls[0]["args"]["tags"]  # type: ignore[index]
        assert isinstance(tags, frozenset)
        assert "a@x.com" not in tags
        assert "[EMAIL_REDACTED]" in tags

    def test_input_not_mutated(self) -> None:
        # _redact_value's contract: always return new, never mutate input.
        original_args: dict[str, object] = {
            "to": "user@test.com",
            "cc": ["a@x.com", "b@y.com"],
        }
        event = _llm_event(
            tool_calls=[{"id": "tc-1", "name": "send", "args": original_args}],
        )
        redactor = PiiRedactor()
        _ = redactor.redact_event(event, in_place=False)
        # The input dict and its nested list are untouched.
        assert original_args["to"] == "user@test.com"
        assert original_args["cc"] == ["a@x.com", "b@y.com"]

    def test_non_string_scalars_pass_through(self) -> None:
        event = _llm_event(
            tool_calls=[
                {
                    "id": "tc-1",
                    "name": "f",
                    "args": {"count": 42, "ratio": 0.5, "ok": True, "x": None},
                },
            ],
        )
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        args = redacted.data.tool_calls[0]["args"]
        assert args == {"count": 42, "ratio": 0.5, "ok": True, "x": None}


class TestPiiRedactorTool:
    def test_redacts_input(self) -> None:
        event = _tool_event(input_str='{"email": "user@test.com"}')
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, ToolPairData)
        assert "user@test.com" not in redacted.data.input

    def test_redacts_output(self) -> None:
        event = _tool_event(output_str="result: 555-123-4567")
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, ToolPairData)
        assert "555-123-4567" not in redacted.data.output


class TestPiiRedactorParentContext:
    def test_redacts_parent_prompts(self) -> None:
        parent = ParentContext(
            agent_id="parent-agent",
            system_prompt="send to admin@corp.com",
            user_instruction="call 555-123-4567",
        )
        event = _llm_event(parent=parent)
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert redacted.parent is not None
        assert "admin@corp.com" not in redacted.parent.system_prompt
        assert "555-123-4567" not in redacted.parent.user_instruction

    def test_none_parent_no_error(self) -> None:
        event = _llm_event(parent=None)
        redactor = PiiRedactor()
        redacted = redactor.redact_event(event)
        assert redacted.parent is None


class TestPiiRedactorCopyBehavior:
    def test_copy_preserves_original(self) -> None:
        event = _llm_event(output="email: user@test.com")
        original_output = event.data.output  # type: ignore[union-attr]
        redactor = PiiRedactor()
        _ = redactor.redact_event(event, in_place=False)
        assert isinstance(event.data, LlmPairData)
        assert event.data.output == original_output

    def test_in_place_mutates(self) -> None:
        event = _llm_event(output="email: user@test.com")
        redactor = PiiRedactor()
        returned = redactor.redact_event(event, in_place=True)
        assert returned is event
        assert isinstance(event.data, LlmPairData)
        assert "user@test.com" not in event.data.output


class TestPiiRedactorConfig:
    def test_mask_strategy(self) -> None:
        event = _llm_event(output="email: john@example.com")
        redactor = PiiRedactor(PiiConfig(strategy=RedactionStrategy.MASK))
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        assert "j***@***.com" in redacted.data.output

    def test_hash_strategy(self) -> None:
        event = _llm_event(output="email: john@example.com")
        redactor = PiiRedactor(PiiConfig(strategy=RedactionStrategy.HASH))
        redacted = redactor.redact_event(event)
        assert isinstance(redacted.data, LlmPairData)
        assert "[EMAIL:" in redacted.data.output


class _Collector:
    def __init__(self) -> None:
        self.events: list[PairedEvent] = []

    async def on_paired_event(self, event: PairedEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


class TestRedactingHandlerCopyOnHit:
    async def test_clean_event_forwarded_without_copy(self) -> None:
        # No PII anywhere → handler should skip the deepcopy and
        # forward the SAME object reference downstream.
        event = _llm_event(output="hello world", system_prompt="you are helpful")
        inner = _Collector()
        handler = RedactingHandler(inner)
        await handler.on_paired_event(event)
        assert len(inner.events) == 1
        assert inner.events[0] is event

    async def test_dirty_event_copies_and_redacts(self) -> None:
        event = _llm_event(output="email user@test.com")
        original_output = event.data.output  # type: ignore[union-attr]
        inner = _Collector()
        handler = RedactingHandler(inner)
        await handler.on_paired_event(event)
        assert len(inner.events) == 1
        forwarded = inner.events[0]
        assert forwarded is not event  # deep-copied
        assert isinstance(forwarded.data, LlmPairData)
        assert "user@test.com" not in forwarded.data.output
        # original untouched
        assert isinstance(event.data, LlmPairData)
        assert event.data.output == original_output
