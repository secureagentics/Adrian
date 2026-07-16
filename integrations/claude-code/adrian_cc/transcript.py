"""Transcript parser - extracts reasoning, user prompts, and invocation context.

Claude Code's transcript is a JSONL file where each line is a
conversation entry.  User turns have ``type == "user"`` (current CC;
``"human"`` is accepted as a legacy alias).  Note that tool results are
also written as ``type == "user"`` entries whose content is a
``tool_result`` block - those are NOT user prompts and are excluded.
This module parses it to extract:
  - Thinking blocks (Claude's internal reasoning)
  - User messages (for invocation_id generation and user_instruction)
  - Message counts (for invocation tracking)
  - System prompt context

The parser caches results and only re-parses when the file changes
(mtime check) to avoid re-reading on every hook call.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
from dataclasses import dataclass, field
from typing import Any, cast

logger = logging.getLogger("adrian_cc.transcript")


@dataclass(slots=True)
class TranscriptState:
    """Parsed state from a transcript file."""

    # All real user prompts in order (tool_result echoes excluded).
    user_messages: list[str] = field(default_factory=list)
    # All assistant thinking blocks in order.
    reasoning_blocks: list[str] = field(default_factory=list)
    # The system prompt if found.
    system_prompt: str = ""
    # promptId of the most recent user prompt - one per user turn, stable across
    # all tool calls in that turn. The preferred invocation_id source.
    last_prompt_id: str = ""
    # File mtime when last parsed, for cache invalidation.
    mtime: float = 0.0


# Cache: transcript_path → parsed state.
_cache: dict[str, TranscriptState] = {}


def _blocks(content: Any) -> list[Any]:
    """The block list of a message ``content`` value, or [] when not a list."""
    if isinstance(content, list):
        return cast(list[Any], content)
    return []


def _field(block: Any, key: str) -> str:
    """A string field from a dict content block; '' when block is not a dict."""
    if isinstance(block, dict):
        return cast(dict[str, Any], block).get(key, "")
    return ""


def _user_prompt_text(content: Any) -> str:
    """Extract a real user prompt's text from an entry's content.

    Returns '' when the entry is not a genuine prompt - in particular a
    ``tool_result`` echo (Claude Code writes tool results as ``type:"user"``
    entries whose content is a list of ``tool_result`` blocks). A real prompt
    is either a plain string or a list of ``text`` blocks.
    """
    if isinstance(content, str):
        return content
    blocks = _blocks(content)
    # A tool_result echo is a user-role entry but not a prompt.
    if any(_field(b, "type") == "tool_result" for b in blocks):
        return ""
    parts = [_field(b, "text") for b in blocks if _field(b, "type") == "text"]
    return " ".join(p for p in parts if p)


def parse_transcript(transcript_path: str) -> TranscriptState:
    """Parse a transcript file, returning cached results if unchanged.

    Returns a TranscriptState with all extracted context.  Returns an
    empty state if the path is empty or the file can't be read.
    """
    if not transcript_path:
        return TranscriptState()

    try:
        mtime = os.path.getmtime(transcript_path)
    except OSError:
        return TranscriptState()

    cached = _cache.get(transcript_path)
    if cached and cached.mtime == mtime:
        return cached

    state = TranscriptState(mtime=mtime)

    try:
        with open(transcript_path, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    entry = json.loads(line)
                except json.JSONDecodeError:
                    continue

                entry_type = entry.get("type", "")
                message = entry.get("message", {})
                content = message.get("content", "")

                # "user" is current Claude Code; "human" is a legacy alias.
                match entry_type:
                    case "user" | "human":
                        if entry.get("isMeta"):
                            continue  # system-injected meta turn, not a real prompt
                        text = _user_prompt_text(content)
                        if text:
                            state.user_messages.append(text)
                            prompt_id = entry.get("promptId", "")
                            if prompt_id:
                                state.last_prompt_id = prompt_id

                    case "assistant":
                        for block in _blocks(content):
                            if _field(block, "type") == "thinking":
                                thinking = _field(block, "thinking")
                                if thinking:
                                    state.reasoning_blocks.append(thinking)

                    case "system":
                        if isinstance(content, str) and content:
                            state.system_prompt = content
                        elif isinstance(content, list):
                            # System messages can be a list of text blocks.
                            parts = [
                                b if isinstance(b, str) else _field(b, "text")
                                for b in _blocks(content)
                            ]
                            joined = "\n".join(p for p in parts if p)
                            if joined:
                                state.system_prompt = joined

                    case _:
                        pass

    except (FileNotFoundError, PermissionError, OSError) as exc:
        logger.debug("Could not read transcript %s: %s", transcript_path, exc)

    _cache[transcript_path] = state
    return state


def get_invocation_id(session_id: str, transcript_path: str) -> str:
    """Derive a stable invocation_id for the current user prompt.

    An invocation = one user prompt → all tool calls until the next prompt.

    Prefers Claude Code's ``promptId`` (one per user turn, stable across every
    tool call in the turn - the ideal invocation key). Falls back to
    session_id + user-message count when the transcript predates promptId, and
    finally to the raw session_id when no transcript/prompt is available.

    NB: before this was fixed the parser matched ``type=="human"`` (which CC
    never emits), so msg_count was always 0 and this collapsed to session_id -
    merging every prompt of a conversation into one classifier window.
    """
    state = parse_transcript(transcript_path)
    if state.last_prompt_id:
        return state.last_prompt_id

    msg_count = len(state.user_messages)
    if msg_count == 0:
        return session_id

    # Legacy fallback: hash session + message count for a compact, stable ID.
    raw = f"{session_id}:invocation:{msg_count}"
    return hashlib.sha256(raw.encode()).hexdigest()[:16]


def get_user_instruction(transcript_path: str) -> str:
    """Return the most recent user message (the current instruction)."""
    state = parse_transcript(transcript_path)
    if state.user_messages:
        return state.user_messages[-1]
    return ""


def get_system_prompt(transcript_path: str) -> str:
    """Return the system prompt from the transcript, if present."""
    return parse_transcript(transcript_path).system_prompt


def get_latest_reasoning(transcript_path: str, max_len: int = 4096) -> str:
    """Return the most recent thinking block, truncated to max_len."""
    state = parse_transcript(transcript_path)
    if state.reasoning_blocks:
        return state.reasoning_blocks[-1][:max_len]
    return ""
