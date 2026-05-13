"""End-to-end parent-context derivation per multi-agent scenario (S1–S8).

Fires the LangChain-shaped callback sequence each scenario produces -
with the ``langgraph_checkpoint_ns`` metadata LangGraph would emit -
through ``AdrianCallbackHandler``, collects the resulting
``PairedEvent`` objects, and asserts each one's ``agent_id`` and
``parent`` identity.

No real LLM calls.  This complements ``test_block_mode_races.py``:
those tests validate block-mode correlation under hand-built
PairedEvent streams; these tests validate the SDK's own parent-context
derivation from realistic callback metadata.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid4

import pytest
from adrian.config import AdrianConfig
from adrian.context import AgentContextTracker
from adrian.format.types import PairedEvent
from adrian.handler import AdrianCallbackHandler
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
    """Test EventHandler that appends paired events to a list."""

    def __init__(self) -> None:
        self.events: list[PairedEvent] = []

    async def on_paired_event(self, event: PairedEvent) -> None:
        self.events.append(event)

    async def close(self) -> None:
        return None


@pytest.fixture()  # pyright: ignore[reportUntypedFunctionDecorator]
def handler_and_events() -> tuple[AdrianCallbackHandler, list[PairedEvent]]:
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


# ---------------------------------------------------------------------------
# Callback-sequence helpers
# ---------------------------------------------------------------------------


def _msgs(system: str, user: str) -> list[list[BaseMessage]]:
    """Build a [[SystemMessage, HumanMessage]] batch shaped like LangGraph emits."""
    return [[SystemMessage(content=system), HumanMessage(content=user)]]


def _ai_with_tool(
    content: str, tool_name: str, tool_call_id: str, args: dict[str, Any] | None = None
) -> AIMessage:
    return AIMessage(
        content=content,
        tool_calls=[
            {"id": tool_call_id, "name": tool_name, "args": args or {}},
        ],
    )


async def _llm_turn(
    handler: AdrianCallbackHandler,
    *,
    ns: str,
    system_prompt: str,
    user_instruction: str,
    output_message: BaseMessage,
    run_id: UUID | None = None,
    parent_run_id: UUID | None = None,
) -> UUID:
    """Fire one LLM callback pair (start + end) with LangGraph-shaped metadata."""
    rid = run_id or uuid4()
    await handler.on_chat_model_start(
        serialized={"kwargs": {"model_name": "ChatAnthropic"}},
        messages=_msgs(system_prompt, user_instruction),
        run_id=rid,
        parent_run_id=parent_run_id,
        metadata={"langgraph_checkpoint_ns": ns},
    )
    await handler.on_llm_end(
        response=LLMResult(generations=[[ChatGeneration(message=output_message)]]),
        run_id=rid,
    )

    return rid


# ---------------------------------------------------------------------------
# Scenario tests, assert agent_id + parent per emitted PairedEvent
# ---------------------------------------------------------------------------


class TestS1SubagentsAsTools:
    """Director delegates to a worker-as-tool; worker's LLM should have parent=director."""

    async def test_worker_carries_director_as_parent(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        # Director LLM, delegates to worker via a tool call
        await _llm_turn(
            handler,
            ns="director:aa",
            system_prompt="You are the director.",
            user_instruction="Delegate work.",
            output_message=_ai_with_tool("delegating", "call_worker", "tc-1"),
        )

        # Worker LLM nested inside director's tool call
        await _llm_turn(
            handler,
            ns="director:aa|worker:bb",
            system_prompt="You are the worker.",
            user_instruction="Do the task.",
            output_message=AIMessage(content="done"),
        )

        assert [e.agent.agent_id for e in events] == ["director", "director|worker"]

        director_event, worker_event = events
        assert director_event.parent is None
        assert worker_event.parent is not None
        assert worker_event.parent.agent_id == "director"
        assert worker_event.parent.system_prompt == "You are the director."
        assert worker_event.parent.user_instruction == "Delegate work."


class TestS2Handoff:
    """Triage transfers to specialist via a handoff tool call."""

    async def test_specialist_carries_triage_as_parent(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        await _llm_turn(
            handler,
            ns="triage:aa",
            system_prompt="You are triage.",
            user_instruction="Route this query.",
            output_message=_ai_with_tool(
                "handing off", "transfer_to_specialist", "tc-t1"
            ),
        )
        await _llm_turn(
            handler,
            ns="specialist:bb",
            system_prompt="You are the specialist.",
            user_instruction="Answer the query.",
            output_message=AIMessage(content="here's your answer"),
        )

        assert [e.agent.agent_id for e in events] == ["triage", "specialist"]
        assert events[0].parent is None
        assert events[1].parent is not None
        assert events[1].parent.agent_id == "triage"
        assert events[1].parent.system_prompt == "You are triage."


class TestS3RouterPeers:
    """A deterministic router dispatches to N specialists, all peers, no parent."""

    async def test_parallel_specialists_have_no_parent(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        # Router is not an agent, it fires no callbacks.  Specialists each
        # run independently; no prior LLM called mark_delegated.
        await _llm_turn(
            handler,
            ns="math_agent:aa",
            system_prompt="You do math.",
            user_instruction="2+2?",
            output_message=AIMessage(content="4"),
        )
        await _llm_turn(
            handler,
            ns="writing_agent:bb",
            system_prompt="You write.",
            user_instruction="Compose.",
            output_message=AIMessage(content="prose"),
        )
        await _llm_turn(
            handler,
            ns="search_agent:cc",
            system_prompt="You search.",
            user_instruction="Find X.",
            output_message=AIMessage(content="found"),
        )

        assert all(e.parent is None for e in events)
        assert [e.agent.agent_id for e in events] == [
            "math_agent",
            "writing_agent",
            "search_agent",
        ]


class TestS4Hierarchical:
    """Director → team_lead → worker (3-level nested delegation)."""

    async def test_three_level_parent_chain(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        await _llm_turn(
            handler,
            ns="director:aa",
            system_prompt="You are the director.",
            user_instruction="Plan.",
            output_message=_ai_with_tool("delegating", "call_team_lead", "tc-d"),
        )
        await _llm_turn(
            handler,
            ns="director:aa|team_lead:bb",
            system_prompt="You are team lead.",
            user_instruction="Break it down.",
            output_message=_ai_with_tool("delegating", "call_worker", "tc-l"),
        )
        await _llm_turn(
            handler,
            ns="director:aa|team_lead:bb|worker:cc",
            system_prompt="You are the worker.",
            user_instruction="Execute.",
            output_message=AIMessage(content="done"),
        )

        director, lead, worker = events
        assert director.agent.agent_id == "director"
        assert director.parent is None

        assert lead.agent.agent_id == "director|team_lead"
        assert lead.parent is not None
        assert lead.parent.agent_id == "director"

        assert worker.agent.agent_id == "director|team_lead|worker"
        assert worker.parent is not None
        assert worker.parent.agent_id == "director|team_lead"


class TestS6SwarmSetOnceRule:
    """Alice → Bob → Alice: Alice keeps parent=None on re-appearance."""

    async def test_alice_stays_top_level_after_handback(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        # Alice turn 1, hands off to Bob via tool call
        await _llm_turn(
            handler,
            ns="Alice:aa|agent:bb",
            system_prompt="You are Alice.",
            user_instruction="Start.",
            output_message=_ai_with_tool("handoff", "transfer_to_Bob", "tc-a1"),
        )
        # Bob turn 1, hands back to Alice
        await _llm_turn(
            handler,
            ns="Bob:cc|agent:dd",
            system_prompt="You are Bob.",
            user_instruction="Continue.",
            output_message=_ai_with_tool("handback", "transfer_to_Alice", "tc-b1"),
        )
        # Alice turn 2, no new parent (set-once rule)
        await _llm_turn(
            handler,
            ns="Alice:aa|agent:bb",
            system_prompt="You are Alice.",
            user_instruction="Finish.",
            output_message=AIMessage(content="final"),
        )

        a1, b1, a2 = events
        assert a1.parent is None
        assert b1.parent is not None and b1.parent.agent_id == "Alice|agent"
        assert a2.parent is None, (
            "Alice's re-appearance keeps parent=None (set-once rule)"
        )


class TestS7Supervisor:
    """Supervisor dispatches to N workers; each worker has supervisor as parent."""

    async def test_each_worker_carries_supervisor_as_parent(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        # Supervisor dispatches to worker 0
        await _llm_turn(
            handler,
            ns="supervisor:aa",
            system_prompt="You supervise.",
            user_instruction="Dispatch.",
            output_message=_ai_with_tool("dispatching", "call_worker_0", "tc-0"),
        )
        await _llm_turn(
            handler,
            ns="supervisor:aa|worker_0:bb",
            system_prompt="You are worker 0.",
            user_instruction="Task 0.",
            output_message=AIMessage(content="done 0"),
        )
        # Supervisor dispatches to worker 1
        await _llm_turn(
            handler,
            ns="supervisor:aa",
            system_prompt="You supervise.",
            user_instruction="Dispatch next.",
            output_message=_ai_with_tool("dispatching", "call_worker_1", "tc-1"),
        )
        await _llm_turn(
            handler,
            ns="supervisor:aa|worker_1:cc",
            system_prompt="You are worker 1.",
            user_instruction="Task 1.",
            output_message=AIMessage(content="done 1"),
        )

        # 4 events: sup, w0, sup(again, set-once → no parent change), w1
        sup1, w0, sup2, w1 = events
        assert sup1.agent.agent_id == "supervisor"
        assert sup1.parent is None

        assert w0.agent.agent_id == "supervisor|worker_0"
        assert w0.parent is not None and w0.parent.agent_id == "supervisor"

        assert sup2.parent is None  # set-once

        assert w1.agent.agent_id == "supervisor|worker_1"
        assert w1.parent is not None and w1.parent.agent_id == "supervisor"


class TestS8DeepResearchParallel:
    """Supervisor dispatches parallel researchers (same _delegated_by reused)."""

    async def test_parallel_researchers_all_carry_supervisor_as_parent(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        # Supervisor emits one LLM turn that spawns N researchers
        await _llm_turn(
            handler,
            ns="research_supervisor:aa|supervisor:bb",
            system_prompt="You coordinate research.",
            user_instruction="Research X in parallel.",
            output_message=AIMessage(
                content="dispatching",
                tool_calls=[
                    {"id": "tc-r0", "name": "research", "args": {"topic": "a"}},
                    {"id": "tc-r1", "name": "research", "args": {"topic": "b"}},
                    {"id": "tc-r2", "name": "research", "args": {"topic": "c"}},
                ],
            ),
        )
        # Researchers fire concurrently (order arbitrary, but _delegated_by
        # stays set across all of them because agent_id != _delegated_by).
        for i in range(3):
            await _llm_turn(
                handler,
                ns=f"research_supervisor:aa|supervisor:bb|supervisor_tools:cc|{i}|researcher:dd",
                system_prompt=f"You are researcher {i}.",
                user_instruction=f"Research topic {chr(ord('a') + i)}.",
                output_message=AIMessage(content=f"result {i}"),
            )

        sup, *researchers = events

        assert sup.agent.agent_id == "research_supervisor|supervisor"
        assert sup.parent is None

        for i, r in enumerate(researchers):
            expected = f"research_supervisor|supervisor|supervisor_tools|{i}|researcher"
            assert r.agent.agent_id == expected
            assert r.parent is not None
            assert r.parent.agent_id == "research_supervisor|supervisor"


class TestS8GraphEdgeDelegation:
    """S8 real shape: supervisor's LLM emits NO tool_calls; dispatching happens
    in a plain async graph node via asyncio.gather.  The path-prefix fallback
    in AgentContextTracker.update() must still infer supervisor as the
    researchers' parent.
    """

    async def test_researchers_carry_supervisor_via_prefix_fallback(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        # Supervisor LLM turn, plain AIMessage, NO tool_calls (key difference
        # from the original S8 test above).  mark_delegated never fires.
        await _llm_turn(
            handler,
            ns="research_supervisor:aa|supervisor:bb",
            system_prompt="You coordinate research.",
            user_instruction="Plan the research.",
            output_message=AIMessage(content="Topic A and Topic B"),
        )

        # Two researchers spawned by a graph-edge (supervisor_tools async
        # function using asyncio.gather).  Their ns paths carry
        # supervisor_tools as a middle segment, NOT supervisor.
        for i in range(2):
            await _llm_turn(
                handler,
                ns=(f"research_supervisor:aa|supervisor_tools:cc|{i}|researcher:dd"),
                system_prompt=f"You are researcher {i}.",
                user_instruction=f"Research topic {chr(ord('a') + i)}.",
                output_message=AIMessage(content=f"result {i}"),
            )

        sup, r0, r1 = events

        assert sup.agent.agent_id == "research_supervisor|supervisor"
        assert sup.parent is None

        for i, r in enumerate((r0, r1)):
            expected_id = f"research_supervisor|supervisor_tools|{i}|researcher"
            assert r.agent.agent_id == expected_id
            assert r.parent is not None, (
                "researcher must inherit supervisor via prefix fallback"
            )
            assert r.parent.agent_id == "research_supervisor|supervisor"
            assert r.parent.system_prompt == "You coordinate research."


# ---------------------------------------------------------------------------
# Sanity: top-level singleton agent never gets a parent
# ---------------------------------------------------------------------------


class TestTopLevelAgent:
    async def test_single_agent_no_parent(
        self,
        handler_and_events: tuple[AdrianCallbackHandler, list[PairedEvent]],
    ) -> None:
        handler, events = handler_and_events

        await _llm_turn(
            handler,
            ns="reason:aa",
            system_prompt="You reason.",
            user_instruction="Think.",
            output_message=AIMessage(content="thought"),
        )

        assert len(events) == 1
        assert events[0].agent.agent_id == "reason"
        assert events[0].parent is None
