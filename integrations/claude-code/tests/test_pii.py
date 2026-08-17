# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

# pyright: reportPrivateUsage=false
"""Tests for PII redaction - engine parity, protobuf redaction, egress wiring."""

import json
import os
from pathlib import Path
from typing import Any

import pytest
from adrian_cc import agent
from adrian_cc.pii import (
    PiiConfig,
    PiiType,
    RedactionStrategy,
    redact_event,
    redact_text,
)
from adrian_cc.proto import event_pb2 as pb

# ---------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------

EMAIL = "alice@example.com"
SSN = "123-45-6789"
AWS_KEY = "AKIAIOSFODNN7EXAMPLE"


def _make_event(**overrides: Any) -> pb.PairedEvent:
    """A tool event carrying PII in every field the plugin populates."""
    event = pb.PairedEvent(
        event_id="evt-1",
        session_id="sess-1",
        pair_type=pb.PAIR_TYPE_TOOL,
    )
    event.agent.agent_id = "claude-code"
    event.agent.system_prompt = f"You help {EMAIL}"
    event.agent.user_instruction = f"Email {EMAIL} about the invoice"
    event.tool.tool_name = "Read"
    event.tool.input = json.dumps({"file_path": "/tmp/customers.csv"})
    event.tool.output = f"name,ssn\nAlice,{SSN}\n"
    event.metadata_json = json.dumps(
        {
            "source": "claude-code",
            "cwd": "/Users/dev/project",
            "reasoning_latest": f"The user's key is {AWS_KEY}",
            "reasoning_block_count": 3,
        }
    ).encode()

    for field, value in overrides.items():
        setattr(event, field, value)

    return event


# ---------------------------------------------------------------
# Vendored engine
# ---------------------------------------------------------------


class TestEngine:
    def test_email_replaced(self) -> None:
        assert redact_text(f"ping {EMAIL}").text == "ping [EMAIL_REDACTED]"

    def test_ssn_replaced(self) -> None:
        assert redact_text(f"ssn {SSN}").text == "ssn [SSN_REDACTED]"

    def test_aws_key_replaced(self) -> None:
        assert redact_text(AWS_KEY).text == "[AWS_KEY_REDACTED]"

    def test_clean_text_untouched(self) -> None:
        text = "just a normal sentence with no secrets"
        assert redact_text(text).text == text

    def test_empty_text(self) -> None:
        assert redact_text("").text == ""

    def test_detections_reported(self) -> None:
        result = redact_text(f"{EMAIL} and {SSN}")
        assert {d.pii_type for d in result.detections} == {
            PiiType.EMAIL,
            PiiType.SSN,
        }

    def test_mask_strategy(self) -> None:
        config = PiiConfig(strategy=RedactionStrategy.MASK)
        assert redact_text(EMAIL, config).text == "a***@***.com"

    def test_enabled_types_filter(self) -> None:
        config = PiiConfig(enabled_types=frozenset({PiiType.SSN}))
        out = redact_text(f"{EMAIL} {SSN}", config).text
        assert EMAIL in out
        assert SSN not in out


# ---------------------------------------------------------------
# Protobuf event redaction
# ---------------------------------------------------------------


class TestRedactEvent:
    def test_agent_context_redacted(self) -> None:
        event = _make_event()
        redact_event(event)
        assert EMAIL not in event.agent.system_prompt
        assert EMAIL not in event.agent.user_instruction
        assert "[EMAIL_REDACTED]" in event.agent.user_instruction

    def test_tool_output_redacted(self) -> None:
        event = _make_event()
        redact_event(event)
        assert SSN not in event.tool.output
        assert "[SSN_REDACTED]" in event.tool.output

    def test_tool_input_redacted(self) -> None:
        event = _make_event()
        event.tool.input = json.dumps({"command": f"mail {EMAIL}"})
        redact_event(event)
        assert EMAIL not in event.tool.input

    def test_tool_input_stays_valid_json(self) -> None:
        event = _make_event()
        event.tool.input = json.dumps({"command": f"mail {EMAIL}"})
        redact_event(event)
        assert json.loads(event.tool.input)["command"] == "mail [EMAIL_REDACTED]"

    def test_parent_context_redacted(self) -> None:
        event = _make_event()
        event.parent.agent_id = "parent-1"
        event.parent.user_instruction = f"Contact {EMAIL}"
        redact_event(event)
        assert EMAIL not in event.parent.user_instruction

    def test_absent_parent_not_materialised(self) -> None:
        event = _make_event()
        redact_event(event)
        assert not event.HasField("parent")

    def test_structural_fields_preserved(self) -> None:
        event = _make_event()
        redact_event(event)
        assert event.event_id == "evt-1"
        assert event.session_id == "sess-1"
        assert event.tool.tool_name == "Read"

    def test_metadata_reasoning_redacted(self) -> None:
        event = _make_event()
        redact_event(event)
        meta = json.loads(event.metadata_json)
        assert AWS_KEY not in meta["reasoning_latest"]
        assert "[AWS_KEY_REDACTED]" in meta["reasoning_latest"]

    def test_metadata_structural_keys_preserved(self) -> None:
        event = _make_event()
        redact_event(event)
        meta = json.loads(event.metadata_json)
        assert meta["cwd"] == "/Users/dev/project"
        assert meta["source"] == "claude-code"
        assert meta["reasoning_block_count"] == 3

    def test_corrupt_metadata_left_alone(self) -> None:
        event = _make_event()
        event.metadata_json = b"not json at all"
        redact_event(event)
        assert event.metadata_json == b"not json at all"

    def test_clean_event_byte_identical(self) -> None:
        event = pb.PairedEvent(
            event_id="evt-2",
            session_id="sess-2",
            pair_type=pb.PAIR_TYPE_TOOL,
        )
        event.agent.agent_id = "claude-code"
        event.agent.user_instruction = "list the files in this directory"
        event.tool.tool_name = "Bash"
        event.tool.input = json.dumps({"command": "ls -la"})
        event.tool.output = "README.md\npyproject.toml\n"
        before = event.SerializeToString()

        redact_event(event)

        assert event.SerializeToString() == before

    def test_llm_pair_redacted(self) -> None:
        event = pb.PairedEvent(
            event_id="evt-3",
            session_id="sess-3",
            pair_type=pb.PAIR_TYPE_LLM,
        )
        event.agent.agent_id = "claude-code"
        msg = event.llm.messages.add()
        msg.role = "human"
        msg.content = f"my email is {EMAIL}"
        event.llm.output = f"noted, {EMAIL}"
        call = event.llm.tool_calls.add()
        call.name = "send_mail"
        call.args = json.dumps({"to": EMAIL})

        redact_event(event)

        assert EMAIL not in event.llm.messages[0].content
        assert EMAIL not in event.llm.output
        assert EMAIL not in event.llm.tool_calls[0].args

    def test_custom_config_honoured(self) -> None:
        event = _make_event()
        redact_event(event, PiiConfig(strategy=RedactionStrategy.HASH))
        assert "[EMAIL:" in event.agent.user_instruction


# ---------------------------------------------------------------
# Egress wiring
# ---------------------------------------------------------------


class TestEgressWiring:
    """Redaction must happen before anything touches the socket."""

    def test_redacted_before_transport(self, monkeypatch: pytest.MonkeyPatch) -> None:
        event = _make_event()

        def _refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("no network in tests")

        monkeypatch.setattr(agent.websockets, "connect", _refuse)

        result = agent._send_event_sync(event, "sess-1", wait_for_verdict=False)

        # Transport failed, yet the event object was already scrubbed.
        assert result["error"]
        assert EMAIL not in event.agent.user_instruction
        assert SSN not in event.tool.output
        assert AWS_KEY not in event.metadata_json.decode()

    def test_real_builder_output_is_redacted(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """End to end through _build_event, the plugin's only event builder."""
        hook_data: dict[str, Any] = {
            "session_id": "test-session-pii",
            "hook_event_name": "PreToolUse",
            "tool_name": "Bash",
            "tool_input": {"command": f"echo {SSN} | mail {EMAIL}"},
            "tool_use_id": "toolu_pii",
            "cwd": "/Users/dev/project",
            "transcript_path": "",
            "permission_mode": "default",
        }
        state: dict[str, Any] = {
            "agent_stack": [{"agent_id": "claude-code", "spawn_id": ""}]
        }
        event = agent._build_event(hook_data, state)
        assert SSN in event.tool.input, "precondition: builder keeps raw input"

        def _refuse(*_args: Any, **_kwargs: Any) -> Any:
            raise OSError("no network in tests")

        monkeypatch.setattr(agent.websockets, "connect", _refuse)
        agent._send_event_sync(event, "test-session-pii", wait_for_verdict=False)

        assert SSN not in event.tool.input
        assert EMAIL not in event.tool.input


# ---------------------------------------------------------------
# Drift guard against the SDK source
# ---------------------------------------------------------------

_SDK_PII = Path(__file__).resolve().parents[3] / "sdk" / "python" / "adrian" / "pii"

sdk_present = pytest.mark.skipif(
    not _SDK_PII.is_dir(),
    reason="SDK source not present (installed plugin, not the monorepo)",
)


@sdk_present
@pytest.mark.parametrize("module", ["_patterns.py", "_strategies.py", "_engine.py"])
def test_vendored_engine_matches_sdk(module: str) -> None:
    """The vendored engine must not drift from sdk/python/adrian/pii.

    Compares everything except the import lines (rewired to adrian_cc)
    and the docstring provenance note added to the copy.
    """
    vendored = Path(agent.__file__).parent / "pii" / module

    def _logic(path: Path) -> list[str]:
        lines = path.read_text(encoding="utf-8").splitlines()
        return [
            line
            for line in lines
            if not line.startswith(("from adrian", "Vendored", "plugin ships"))
        ]

    sdk_lines = _logic(_SDK_PII / module)
    plugin_lines = _logic(vendored)

    # Drop the provenance paragraph the copies carry in their docstring.
    extra = len(plugin_lines) - len(sdk_lines)
    assert extra >= 0, f"{module}: vendored copy is missing lines"

    missing = [line for line in sdk_lines if line not in plugin_lines]
    assert not missing, f"{module} drifted from the SDK: {missing[:5]}"


def test_pii_package_has_no_third_party_imports() -> None:
    """The vendored engine must stay stdlib-only so it needs no vendoring."""
    pii_dir = Path(agent.__file__).parent / "pii"
    allowed = {"adrian_cc", "__future__"}
    stdlib = {
        "dataclasses",
        "enum",
        "hashlib",
        "ipaddress",
        "json",
        "re",
        "typing",
    }

    for path in sorted(pii_dir.glob("*.py")):
        for line in path.read_text(encoding="utf-8").splitlines():
            if not line.startswith(("import ", "from ")):
                continue
            root = line.split()[1].split(".")[0]
            assert root in allowed | stdlib, f"{os.path.basename(path)}: {line}"
