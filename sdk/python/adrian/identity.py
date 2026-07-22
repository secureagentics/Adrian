# SPDX-License-Identifier: Apache-2.0
# Copyright (c) 2026 SecureAgentics

"""Agent identity dispatcher.

Derives an ``agent_id`` from callback metadata using framework-specific
adapters. Returns ``"default"`` when no adapter can identify the agent.

Currently only LangGraph is supported. Validated across all 8 multi-agent
scenarios + real-world libraries (langgraph-swarm-py, langgraph-supervisor-py)
plus Opus stress tests with parallel/hierarchical patterns, LangGraph
always provides ``langgraph_checkpoint_ns`` metadata, so the previous
system prompt hash fallback was never triggered and has been removed.

If support is added for frameworks without equivalent metadata (CrewAI,
AutoGen, etc.), reintroduce a fallback here.
"""

from __future__ import annotations

import logging

from adrian.adapters.langgraph import derive_langgraph_agent_id
from adrian.types import CallbackMetadata, ChatMessage

logger = logging.getLogger("adrian.identity")


def derive_agent_id(
    metadata: CallbackMetadata | None,
    messages: list[ChatMessage] | None = None,
) -> str:
    """Derive agent identity from callback metadata.

    Args:
        metadata: Callback metadata from the LangChain event.
        messages: Message list from chat_model_start (unused currently,
            kept for future framework adapters).

    Returns:
        Agent identity string, or ``"default"`` if no adapter matches.
    """
    del messages  # reserved for future non-LangGraph adapters

    if metadata:
        agent_id = derive_langgraph_agent_id(metadata)

        if agent_id is not None:
            return agent_id

    return "default"
