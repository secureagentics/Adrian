# pyright: reportPrivateUsage=false
"""Tests for transcript parser."""

import json
import os
import tempfile
import time
from typing import Any

from adrian_cc.transcript import (
    TranscriptState,
    _cache,
    get_invocation_id,
    get_latest_reasoning,
    get_system_prompt,
    get_user_instruction,
    parse_transcript,
)


def _write_transcript(lines: list[dict[str, Any]]) -> str:
    """Write a JSONL transcript to a temp file, return the path."""
    # delete=False: the file must outlive this helper (its path is returned).
    f = tempfile.NamedTemporaryFile(  # noqa: SIM115
        mode="w", suffix=".jsonl", delete=False, encoding="utf-8"
    )
    for entry in lines:
        f.write(json.dumps(entry) + "\n")
    f.close()
    return f.name


def _cleanup(path: str) -> None:
    _cache.pop(path, None)
    os.unlink(path)


# ---------------------------------------------------------------
# parse_transcript
# ---------------------------------------------------------------


class TestParseTranscript:
    def test_empty_path(self):
        state = parse_transcript("")
        assert isinstance(state, TranscriptState)
        assert state.user_messages == []
        assert state.reasoning_blocks == []
        assert state.system_prompt == ""

    def test_missing_file(self):
        state = parse_transcript("/nonexistent/path.jsonl")
        assert state.user_messages == []

    def test_user_messages(self):
        # Current Claude Code writes user turns as type == "user".
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Fix the login bug"}},
                {"type": "user", "message": {"content": "Now add tests"}},
            ]
        )
        try:
            state = parse_transcript(path)
            assert len(state.user_messages) == 2
            assert state.user_messages[0] == "Fix the login bug"
            assert state.user_messages[1] == "Now add tests"
        finally:
            _cleanup(path)

    def test_legacy_human_alias(self):
        # "human" is accepted as a legacy alias for older transcripts.
        path = _write_transcript(
            [
                {"type": "human", "message": {"content": "Legacy prompt"}},
            ]
        )
        try:
            state = parse_transcript(path)
            assert state.user_messages == ["Legacy prompt"]
        finally:
            _cleanup(path)

    def test_tool_result_user_entry_excluded(self):
        # Tool results are written as type=="user" but are NOT prompts.
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Read the file"}},
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "toolu_1",
                                "content": "file contents",
                            },
                        ]
                    },
                },
            ]
        )
        try:
            state = parse_transcript(path)
            assert state.user_messages == ["Read the file"]  # tool_result skipped
        finally:
            _cleanup(path)

    def test_user_list_text_content(self):
        # A real prompt may arrive as a list of text blocks.
        path = _write_transcript(
            [
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {"type": "text", "text": "Hello"},
                            {"type": "text", "text": "world"},
                        ]
                    },
                },
            ]
        )
        try:
            state = parse_transcript(path)
            assert state.user_messages == ["Hello world"]
        finally:
            _cleanup(path)

    def test_meta_user_entry_skipped(self):
        # isMeta user entries are system-injected, not real prompts.
        path = _write_transcript(
            [
                {
                    "type": "user",
                    "isMeta": True,
                    "message": {"content": "<system-reminder>"},
                },
                {"type": "user", "message": {"content": "Real prompt"}},
            ]
        )
        try:
            state = parse_transcript(path)
            assert state.user_messages == ["Real prompt"]
        finally:
            _cleanup(path)

    def test_captures_prompt_id(self):
        path = _write_transcript(
            [
                {"type": "user", "promptId": "pid-xyz", "message": {"content": "Hi"}},
            ]
        )
        try:
            state = parse_transcript(path)
            assert state.last_prompt_id == "pid-xyz"
        finally:
            _cleanup(path)

    def test_reasoning_blocks(self):
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Explain auth"}},
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "The user wants to understand auth flow.",
                            },
                            {"type": "text", "text": "Here's how auth works..."},
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {
                                "type": "thinking",
                                "thinking": "Now I need to show the code for JWT validation.",
                            },
                            {
                                "type": "tool_use",
                                "name": "Read",
                                "input": {"file_path": "auth.py"},
                            },
                        ]
                    },
                },
            ]
        )
        try:
            state = parse_transcript(path)
            assert len(state.reasoning_blocks) == 2
            assert "auth flow" in state.reasoning_blocks[0]
            assert "JWT validation" in state.reasoning_blocks[1]
        finally:
            _cleanup(path)

    def test_system_prompt_string(self):
        path = _write_transcript(
            [
                {
                    "type": "system",
                    "message": {"content": "You are a helpful assistant."},
                },
                {"type": "user", "message": {"content": "Hello"}},
            ]
        )
        try:
            state = parse_transcript(path)
            assert state.system_prompt == "You are a helpful assistant."
        finally:
            _cleanup(path)

    def test_system_prompt_list(self):
        path = _write_transcript(
            [
                {
                    "type": "system",
                    "message": {
                        "content": [
                            {"text": "Part one."},
                            {"text": "Part two."},
                        ]
                    },
                },
            ]
        )
        try:
            state = parse_transcript(path)
            assert "Part one." in state.system_prompt
            assert "Part two." in state.system_prompt
        finally:
            _cleanup(path)

    def test_caching(self):
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Hello"}},
            ]
        )
        try:
            s1 = parse_transcript(path)
            s2 = parse_transcript(path)
            assert s1 is s2  # same object from cache
        finally:
            _cleanup(path)

    def test_cache_invalidation_on_mtime(self):
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "First"}},
            ]
        )
        try:
            s1 = parse_transcript(path)
            assert len(s1.user_messages) == 1

            # Append to file (changes mtime).
            time.sleep(0.05)
            with open(path, "a") as f:
                f.write(
                    json.dumps({"type": "user", "message": {"content": "Second"}})
                    + "\n"
                )
            os.utime(path, None)

            s2 = parse_transcript(path)
            assert s2 is not s1
            assert len(s2.user_messages) == 2
        finally:
            _cleanup(path)

    def test_malformed_lines_skipped(self):
        path = _write_transcript([])
        with open(path, "a") as f:
            f.write("not json\n")
            f.write('{"type": "user", "message": {"content": "OK"}}\n')
            f.write("{broken\n")
        try:
            state = parse_transcript(path)
            assert len(state.user_messages) == 1
            assert state.user_messages[0] == "OK"
        finally:
            _cleanup(path)

    def test_empty_thinking_blocks_ignored(self):
        path = _write_transcript(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": ""},
                            {"type": "thinking", "thinking": "Real reasoning"},
                        ]
                    },
                },
            ]
        )
        try:
            state = parse_transcript(path)
            assert len(state.reasoning_blocks) == 1
            assert state.reasoning_blocks[0] == "Real reasoning"
        finally:
            _cleanup(path)


# ---------------------------------------------------------------
# get_invocation_id
# ---------------------------------------------------------------


class TestGetInvocationId:
    def test_no_transcript(self):
        inv = get_invocation_id("sess-001", "")
        assert inv == "sess-001"

    def test_prefers_prompt_id(self):
        path = _write_transcript(
            [
                {
                    "type": "user",
                    "promptId": "pid-abc-123",
                    "message": {"content": "Do the thing"},
                },
            ]
        )
        try:
            assert get_invocation_id("sess-001", path) == "pid-abc-123"
        finally:
            _cleanup(path)

    def test_prompt_id_stable_across_turn(self):
        # A prompt and its tool_result share one promptId → one invocation.
        path = _write_transcript(
            [
                {"type": "user", "promptId": "pid-1", "message": {"content": "First"}},
                {
                    "type": "user",
                    "promptId": "pid-1",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t",
                                "content": "out",
                            },
                        ]
                    },
                },
            ]
        )
        try:
            assert get_invocation_id("s", path) == "pid-1"
        finally:
            _cleanup(path)

    def test_prompt_id_changes_with_new_prompt(self):
        path = _write_transcript(
            [
                {"type": "user", "promptId": "pid-1", "message": {"content": "First"}},
                {"type": "user", "promptId": "pid-2", "message": {"content": "Second"}},
            ]
        )
        try:
            assert get_invocation_id("s", path) == "pid-2"  # latest turn wins
        finally:
            _cleanup(path)

    def test_fallback_count_hash_without_prompt_id(self):
        # Older transcripts without promptId → compact session+count hash.
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Do something"}},
            ]
        )
        try:
            a = get_invocation_id("sess-001", path)
            b = get_invocation_id("sess-001", path)
            assert a == b
            assert a != "sess-001"  # hashed, not raw session
            assert len(a) == 16  # sha256 hex truncated
        finally:
            _cleanup(path)

    def test_fallback_changes_with_new_prompt(self):
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "First prompt"}},
            ]
        )
        try:
            inv1 = get_invocation_id("sess-001", path)
        finally:
            _cleanup(path)

        path2 = _write_transcript(
            [
                {"type": "user", "message": {"content": "First prompt"}},
                {"type": "user", "message": {"content": "Second prompt"}},
            ]
        )
        try:
            inv2 = get_invocation_id("sess-001", path2)
            assert inv1 != inv2
        finally:
            _cleanup(path2)

    def test_different_sessions_different_ids(self):
        # No promptId → hash includes session_id → distinct per session.
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Hello"}},
            ]
        )
        try:
            a = get_invocation_id("sess-001", path)
            _cache.pop(path, None)
            b = get_invocation_id("sess-002", path)
            assert a != b
        finally:
            _cleanup(path)


# ---------------------------------------------------------------
# get_user_instruction
# ---------------------------------------------------------------


class TestGetUserInstruction:
    def test_returns_latest(self):
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "First"}},
                {"type": "user", "message": {"content": "Latest"}},
            ]
        )
        try:
            assert get_user_instruction(path) == "Latest"
        finally:
            _cleanup(path)

    def test_tool_result_not_returned(self):
        # The latest real prompt, not a trailing tool_result echo.
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "The real ask"}},
                {
                    "type": "user",
                    "message": {
                        "content": [
                            {
                                "type": "tool_result",
                                "tool_use_id": "t",
                                "content": "result",
                            },
                        ]
                    },
                },
            ]
        )
        try:
            assert get_user_instruction(path) == "The real ask"
        finally:
            _cleanup(path)

    def test_empty_transcript(self):
        assert get_user_instruction("") == ""


# ---------------------------------------------------------------
# get_latest_reasoning
# ---------------------------------------------------------------


class TestGetLatestReasoning:
    def test_returns_latest_block(self):
        path = _write_transcript(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "First thought"},
                        ]
                    },
                },
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "Second thought"},
                        ]
                    },
                },
            ]
        )
        try:
            assert get_latest_reasoning(path) == "Second thought"
        finally:
            _cleanup(path)

    def test_truncation(self):
        path = _write_transcript(
            [
                {
                    "type": "assistant",
                    "message": {
                        "content": [
                            {"type": "thinking", "thinking": "A" * 10000},
                        ]
                    },
                },
            ]
        )
        try:
            r = get_latest_reasoning(path, max_len=100)
            assert len(r) == 100
        finally:
            _cleanup(path)

    def test_no_reasoning(self):
        assert get_latest_reasoning("") == ""


# ---------------------------------------------------------------
# get_system_prompt
# ---------------------------------------------------------------


class TestGetSystemPrompt:
    def test_returns_prompt(self):
        path = _write_transcript(
            [
                {"type": "system", "message": {"content": "Be helpful."}},
            ]
        )
        try:
            assert get_system_prompt(path) == "Be helpful."
        finally:
            _cleanup(path)

    def test_no_system_message(self):
        path = _write_transcript(
            [
                {"type": "user", "message": {"content": "Hi"}},
            ]
        )
        try:
            assert get_system_prompt(path) == ""
        finally:
            _cleanup(path)
