"""Invocation and agent context tracking.

Tracks two concepts that LangChain's callback system does not provide:

1. **Invocation ID**, A UUID that spans an entire user prompt through
   all sub-agent execution. Set once at the top-level ``Pregel.ainvoke``
   via ``contextvars.ContextVar``, inherited automatically by nested
   async calls (sub-agent ``ainvoke``). Every event in one user-prompted
   task shares the same invocation ID.

2. **Agent context**, Each agent's system prompt and user instruction,
   tracked by agent_id. When a different agent_id appears (sub-agent
   started), the previous agent's context becomes the ``ParentContext``
   for the new agent's events.
"""

from __future__ import annotations

from contextvars import ContextVar, Token

from adrian.format.types import AgentContext, ParentContext

_invocation_id: ContextVar[str | None] = ContextVar(
    "adrian_invocation_id",
    default=None,
)


def get_invocation_id() -> str | None:
    """Get the current invocation ID from async context.

    Returns:
        The invocation UUID string, or ``None`` if not inside an
        invocation (no ``Pregel.ainvoke`` on the call stack).
    """
    return _invocation_id.get()


def set_invocation_id(invocation_id: str) -> Token[str | None]:
    """Set the invocation ID in async context.

    Called by the patched ``Pregel.ainvoke`` at the top level. Sub-agent
    ``ainvoke`` calls inherit the same value via contextvars propagation.

    Args:
        invocation_id: UUID string for this invocation.

    Returns:
        Token for resetting the context var when the invocation ends.
    """
    return _invocation_id.set(invocation_id)


class AgentContextTracker:
    """Tracks agent prompt context for parent-child enrichment.

    Maintains a map of ``agent_id → AgentContext`` and detects when the
    active agent changes. When a new agent_id appears, the previous
    agent's context is returned as the ``ParentContext``.

    This enables sub-agent events to carry their parent's system prompt
    and user instruction without needing ``parent_run_id`` chains (which
    are unreliable across all multi-agent patterns).
    """

    def __init__(self) -> None:
        """Initialise with empty context tracking."""
        self._contexts: dict[str, AgentContext] = {}
        self._last_agent_id: str | None = None
        self._parent_map: dict[str, ParentContext | None] = {}
        self._delegated_by: str | None = None

    def mark_delegated(self, agent_id: str) -> None:
        """Signal that an agent's llm_end had tool calls.

        Records which specific agent delegated. The next new agent
        that appears will only get a parent if it was preceded by
        this specific agent. This prevents race conditions in
        parallel execution where one agent's tool call flag could
        be consumed by a different parallel agent.

        Args:
            agent_id: The agent that made the tool call.
        """
        self._delegated_by = agent_id

    def update(
        self,
        agent_id: str,
        system_prompt: str,
        user_instruction: str,
    ) -> ParentContext | None:
        """Update context for an agent and detect parent relationship.

        The parent relationship is set only once, the first time an
        agent_id appears. The previous agent is only treated as a
        parent if it explicitly delegated via tool call (i.e.
        ``mark_delegated`` was called after its llm_end). This
        distinguishes:

        - **Delegation** (S1/S2): LLM decided to call a tool/transfer
          → new agent gets parent.
        - **Graph edges** (S3 router, synthesizer): code dispatched
          the agent → no parent, they're peers.

        Args:
            agent_id: Identity of the agent.
            system_prompt: The agent's system message.
            user_instruction: The last human/user message.

        Returns:
            ``ParentContext`` if a parent relationship exists for this
            agent, or ``None`` if it is a top-level agent or peer.
        """
        self._contexts[agent_id] = AgentContext(
            agent_id=agent_id,
            system_prompt=system_prompt,
            user_instruction=user_instruction,
        )

        if agent_id not in self._parent_map:
            parent: ParentContext | None = None

            if self._delegated_by is not None:
                # A delegation is active. Check if the delegating agent
                # is a known context (it should be, it had a chat_model_start).
                prev = self._contexts.get(self._delegated_by)

                if prev and agent_id != self._delegated_by:
                    parent = ParentContext(
                        agent_id=prev.agent_id,
                        system_prompt=prev.system_prompt,
                        user_instruction=prev.user_instruction,
                    )

            if parent is None:
                # Fallback: graph-edge delegation (no tool_calls emitted by the
                # parent LLM).  S8 deep-research dispatches researchers via
                # asyncio.gather inside a plain async function, so
                # mark_delegated never fires.  Walk _contexts for the best
                # *ancestor* candidate: the previously-seen agent with the
                # LONGEST shared checkpoint_ns prefix, strictly shallower than
                # this agent, after normalising away LangGraph's positional
                # iteration indices (pure-integer segments like ``|1|``).
                # Without the index strip, two gather siblings look like
                # parent/child (``...|researcher`` vs ``...|1|researcher``).

                def _normalize(parts: list[str]) -> list[str]:
                    """Drop LangGraph positional-index segments (pure ints)."""
                    return [p for p in parts if not p.isdigit()]

                raw_new_parts = agent_id.split("|")
                new_parts = _normalize(raw_new_parts)
                best_candidate: str | None = None
                best_common = 0

                for other_id in self._contexts:
                    if other_id == agent_id:
                        continue

                    other_parts = _normalize(other_id.split("|"))

                    if len(other_parts) >= len(new_parts):
                        # Same-depth (after stripping indices) → peer, not parent.
                        continue

                    common = 0

                    for op, np in zip(other_parts, new_parts, strict=False):
                        if op == np:
                            common += 1
                        else:
                            break

                    if common > best_common:
                        best_common = common
                        best_candidate = other_id

                if best_candidate is not None and best_common > 0:
                    prev_ctx = self._contexts.get(best_candidate)

                    if prev_ctx:
                        parent = ParentContext(
                            agent_id=prev_ctx.agent_id,
                            system_prompt=prev_ctx.system_prompt,
                            user_instruction=prev_ctx.user_instruction,
                        )

            self._parent_map[agent_id] = parent

        # Only clear delegation when the delegating agent itself resumes.
        # This allows multiple parallel sub-agents spawned by one delegation
        # to all receive the parent context.
        if agent_id == self._delegated_by:
            self._delegated_by = None

        self._last_agent_id = agent_id

        return self._parent_map.get(agent_id)

    def get_parent(self, agent_id: str) -> ParentContext | None:
        """Get the stored parent context for an agent.

        Used by tool events which inherit the agent_id from the
        preceding LLM event but need the parent context that was
        established when that agent first appeared.

        Args:
            agent_id: Identity of the agent to look up.

        Returns:
            ``ParentContext`` or ``None`` if no parent recorded.
        """
        return self._parent_map.get(agent_id)

    def has_context(self, agent_id: str) -> bool:
        """Return True if a chat_model_start has been seen for ``agent_id``."""
        return agent_id in self._contexts

    def get_context(self, agent_id: str) -> AgentContext | None:
        """Return the stored ``AgentContext`` for ``agent_id``, or ``None``."""
        return self._contexts.get(agent_id)
