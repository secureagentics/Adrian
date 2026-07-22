# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Stateful tracker for Claude Code sessions.

Maintains three pieces of state across hook calls within a daemon:

1. **Invocation tracking** - Detects new user prompts from the transcript
   and assigns stable invocation_ids so the Adrian sliding window groups
   events correctly by (session_id, invocation_id, agent_id).

2. **Agent hierarchy** - Tracks when Claude Code spawns sub-agents via
   the ``Agent`` tool.  Maintains a stack so nested tool calls are
   attributed to the correct agent with proper parent context.

3. **Event pair buffer** - Buffers PreToolUse data by tool_use_id so
   PostToolUse can complete the pair with the tool's output.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from adrian_cc.transcript import (
    get_invocation_id,
    get_latest_reasoning,
    get_system_prompt,
    get_user_instruction,
)

logger = logging.getLogger("adrian_cc.tracker")


@dataclass(slots=True)
class AgentFrame:
    """One frame in the agent hierarchy stack."""

    agent_id: str
    # The tool_use_id of the Agent tool call that spawned this frame.
    # Empty for the root (top-level) agent.
    spawn_tool_use_id: str = ""
    # Description from the Agent tool_input (subagent_type or description).
    description: str = ""


@dataclass(slots=True)
class PreToolBuffer:
    """Buffered PreToolUse data waiting for its PostToolUse pair."""

    event_id: str
    tool_name: str
    tool_input: dict[str, Any]
    tool_use_id: str
    invocation_id: str
    agent_id: str
    parent_agent_id: str
    timestamp: str


class SessionTracker:
    """Tracks state for one Claude Code session across hook calls."""

    def __init__(self) -> None:
        """Initialize an empty tracker with the root agent frame."""
        # Agent hierarchy stack.  Root is always present.
        self._agent_stack: list[AgentFrame] = [
            AgentFrame(agent_id="claude-code"),
        ]
        # Buffered PreToolUse events by tool_use_id.
        self._pre_buffer: dict[str, PreToolBuffer] = {}
        # Last known invocation_id (for dedup).
        self._last_invocation_id: str = ""

    # ------------------------------------------------------------------
    # Agent hierarchy
    # ------------------------------------------------------------------

    @property
    def current_agent_id(self) -> str:
        """The agent_id of the currently active agent."""
        return self._agent_stack[-1].agent_id

    @property
    def parent_agent_id(self) -> str:
        """The agent_id of the parent (empty if at root)."""
        if len(self._agent_stack) >= 2:
            return self._agent_stack[-2].agent_id
        return ""

    def push_agent(self, tool_use_id: str, tool_input: dict[str, Any]) -> str:
        """Enter a sub-agent context when an Agent tool is called.

        Extracts the agent type/description from tool_input and pushes
        a new frame.  Returns the new agent_id.
        """
        # Extract agent identity from the Agent tool's input.
        subagent_type = tool_input.get("subagent_type", "")
        description = tool_input.get("description", "")
        # Build a descriptive agent_id.
        agent_id = subagent_type or "subagent"
        if description:
            # Use first 30 chars of description as suffix for readability.
            slug = description.lower().replace(" ", "-")[:30].rstrip("-")
            agent_id = f"{agent_id}:{slug}"

        frame = AgentFrame(
            agent_id=agent_id,
            spawn_tool_use_id=tool_use_id,
            description=description,
        )
        self._agent_stack.append(frame)
        logger.info(
            "Pushed agent %s (spawned by %s)",
            agent_id,
            tool_use_id,
        )
        return agent_id

    def pop_agent(self, tool_use_id: str) -> str | None:
        """Exit a sub-agent context when the Agent tool completes.

        Returns the popped agent_id, or None if tool_use_id doesn't
        match the current frame (shouldn't happen in normal flow).
        """
        if len(self._agent_stack) <= 1:
            return None  # Never pop root.
        top = self._agent_stack[-1]
        if top.spawn_tool_use_id == tool_use_id:
            self._agent_stack.pop()
            logger.info("Popped agent %s", top.agent_id)
            return top.agent_id
        return None

    # ------------------------------------------------------------------
    # Invocation tracking
    # ------------------------------------------------------------------

    def resolve_invocation_id(self, session_id: str, transcript_path: str) -> str:
        """Get the current invocation_id, detecting new user prompts."""
        inv_id = get_invocation_id(session_id, transcript_path)
        if inv_id != self._last_invocation_id:
            logger.info(
                "New invocation detected: %s (was %s)",
                inv_id,
                self._last_invocation_id or "(none)",
            )
            self._last_invocation_id = inv_id
        return inv_id

    # ------------------------------------------------------------------
    # Agent context extraction
    # ------------------------------------------------------------------

    def build_agent_context(self, transcript_path: str) -> dict[str, Any]:
        """Build the agent context dict for the current agent.

        Returns a dict with agent_id, system_prompt, user_instruction
        populated from transcript state.
        """
        return {
            "agent_id": self.current_agent_id,
            "system_prompt": get_system_prompt(transcript_path)
            or get_latest_reasoning(transcript_path),
            "user_instruction": get_user_instruction(transcript_path),
        }

    def build_parent_context(self, transcript_path: str) -> dict[str, Any]:
        """Build the parent agent context dict.

        Returns empty-string agent_id if at root level (no parent).
        """
        parent_id = self.parent_agent_id
        if not parent_id:
            return {"agent_id": "", "system_prompt": "", "user_instruction": ""}
        return {
            "agent_id": parent_id,
            "system_prompt": "",
            "user_instruction": get_user_instruction(transcript_path),
        }

    # ------------------------------------------------------------------
    # Event pair buffer
    # ------------------------------------------------------------------

    def buffer_pre_event(self, buf: PreToolBuffer) -> None:
        """Store a PreToolUse event for later pairing with PostToolUse."""
        self._pre_buffer[buf.tool_use_id] = buf

    def pop_pre_event(self, tool_use_id: str) -> PreToolBuffer | None:
        """Retrieve and remove a buffered PreToolUse event."""
        return self._pre_buffer.pop(tool_use_id, None)
