# Adrian SDK

**Multi-agent security monitoring SDK for LangChain / LangGraph.**

[Documentation](https://docs.adrian.secureagentics.ai) &nbsp;•&nbsp; [Dashboard](https://adrian.secureagentics.ai) &nbsp;•&nbsp; [Discord](https://discord.gg/Vq2VyYrw8Z) &nbsp;•&nbsp; [LinkedIn](https://www.linkedin.com/company/secure-agentics)

Adrian auto-instruments LangChain / LangGraph and emits **paired events** - each LLM call (`chat_model_start` + `llm_end`) and each tool execution (`tool_start` + `tool_end`) is assembled into a single `PairedEvent` carrying agent identity, parent context, and paired payload. Events stream over WebSocket to the Adrian backend, which classifies them against the MAD policy and returns verdicts. The agent profile's execution mode (Alert / Block / Human Review) is decided server-side; in Block and Human Review modes, malicious LLM decisions are caught *before* their tool calls execute.

## Why Adrian

Most agent monitoring stops at activity logs (APIs, MCP, DB interactions, tool calls). Adrian also analyses the agent's reasoning: understanding _why_ it took an action, under what context, and what it is planning on doing next. [Research by OpenAI and DeepMind](https://arxiv.org/pdf/2503.11926) found that combining behaviour and reasoning analysis like this boosts detection accuracy by around 35% and is 4x more likely to catch nuanced attacks.

The classifier reasons about each action against the agent's stated remit rather than pattern-matching against a labelled prompt-injection dataset. If your e-commerce agent suddenly starts resetting user passwords, that does not appear in any training set, but it is a risk you want flagged.

## Install

```sh
pip install adrian-sdk
```

Requires Python 3.12+.

## Quickstart

```python
import asyncio

import adrian
from langchain_openai import ChatOpenAI


async def main():
    adrian.init(api_key="adr_live_...")

    # Your LangChain / LangGraph code runs normally - every call is captured.
    llm = ChatOpenAI(model="gpt-4o")
    response = await llm.ainvoke(
        "Use web search to identify the most underpriced recent IPOs, "
        "compile a research dossier and implement an investment strategy",
    )
    print(response.content)

    adrian.shutdown()


asyncio.run(main())
```

The SDK defaults to `wss://adrian.secureagentics.ai/ws` (the hosted Adrian backend). Override via `ws_url=` or `ADRIAN_WS_URL` to point at a self-hosted backend, or override the `handlers=` list entirely for JSONL-only / custom transports.

The Quickstart uses the async pattern (`asyncio.run` + `await llm.ainvoke`) because the WebSocket transport runs on the asyncio loop - sync `llm.invoke` returns before the loop has a chance to flush events.

<sup>Last verified with `langchain-core==1.3.3`, `langgraph==1.1.2`, `langchain-openai==1.2.1` (2026-05-08).</sup>

## How it works

1. `adrian.init(...)` monkey-patches LangChain's `Runnable`, `BaseChatModel`, `CallbackManager`, `Pregel`, and `ToolNode` so every invocation routes through Adrian's callback handler.
2. The handler pairs `*_start` + `*_end` callbacks by `run_id`, derives the agent's identity from LangGraph's `checkpoint_ns`, attaches the parent agent's context when delegation occurred, and emits a single `PairedEvent` to all registered handlers.
3. Default handlers are `JSONLHandler` (writes each event as one JSON line to `events.jsonl`) and `WebSocketClient` (sends protobuf frames to the Adrian backend). Override either by passing `handlers=[...]` to `init()`.
4. On connect the server returns a `LoginAck` carrying the agent profile's effective `PolicySnapshot` (mode + per-MAD-code scope booleans). The backend then classifies each event and returns a `Verdict` with a `mad_code` (e.g. `M4_a`) plus the policy snapshot at decision time.
5. In Block and Human Review modes, each `ToolNode` invocation awaits the verdict of the LLM pair that requested it (correlated by `tool_call.id`). When the policy halts, synthetic `ToolMessage` responses are returned to the agent; the real tool never runs.

## Reference

Full reference for `init()` parameters, observer callbacks, and the `PairedEvent` schema lives on the docs site: [SDK reference](https://docs.adrian.secureagentics.ai/reference/sdk).

## Execution modes

The agent profile's execution mode is set in the dashboard and pushed to the SDK in the `LoginAck` frame; there is no client-side switch. The mode plus a `PolicySnapshot` of per-MAD-code scope booleans (`policy_m0`, `policy_m2`, `policy_m3`, `policy_m4`) decide when a tool call should halt.

| Mode | Wire enum | SDK behaviour |
|------|-----------|---------------|
| Alert | `MODE_ALERT` | No wait, no halt. The dashboard logs verdicts; tools run unimpeded. |
| Block | `MODE_BLOCK` | `ToolNode.ainvoke` awaits the verdict of the LLM pair that requested its tool calls. In-scope verdicts (`policy_mN=true` for the verdict's MAD prefix) halt with synthetic `ToolMessage(content="[BLOCKED by security policy]")`; out-of-scope continue. Fail-open after `block_timeout`. |
| Human Review | `MODE_HITL` | Same wait, but indefinite - the server holds the verdict until a human approves or rejects on the dashboard. Approve → continue, reject → halt. Out-of-scope verdicts forward immediately. |

Halt correlation is per-`tool_call.id` - parallel fan-out patterns (S3 router, S8 deep research) wait on each specialist's own verdict, no cross-contamination.

Typical configurations:

- **Block-mode auto-enforce**: `policy_m3=true, policy_m4=true` (optionally `policy_m2=true` for stricter posture).
- **Human Review gating**: `policy_m3=true, policy_m4=true` for human review; M0/M2 silent.
- **Alert observability**: per-MAD-code bools are irrelevant; the dashboard sees everything, the SDK does not gate.

### Human Review durability caveat

Human Review waits are session-scoped and live in the SDK process. If the SDK restarts before the dashboard resolves a pending review, the resolution is dropped on arrival (logged at WARN) and the agent has no live future to wake. The audit trail in the dashboard's review queue survives - recovery to a live agent does not.

### Catch-on-next-turn for tool-output attacks

The classifier targets LLM pairs; tool outputs themselves are not directly classified. A tool-side attack - a benign-looking call whose *output* contains prompt injection or exfiltrated data - still fails: the classifier sees the poisoned output in the *next* LLM turn's input messages, flags the induced reasoning, and blocks the follow-up tool before it runs. No bypass as long as the agent is the only actor making tool calls.

## Multi-agent support

Parent context is derived from the `AgentContextTracker`:

- **Delegation via tool call** (S1 subagents-as-tools, S2 handoff, S4 hierarchical, S7 supervisor): the LLM's `tool_calls` mark it as the delegating agent; the next new agent that appears gets that agent as `parent`.
- **Parallel siblings spawned by one delegation** (S8 deep research): all children inherit the same parent until the delegating agent itself resumes.
- **Code-dispatched peers** (S3 router fan-out, S5 custom workflow): no delegation → no parent. Peers all have `parent=None`.
- **Set-once** (S6 swarm handbacks): an agent's `parent` is fixed on first appearance and never changes, even across Alice ↔ Bob ↔ Alice handbacks.

End-to-end scenario tests at `tests/test_parent_context_scenarios.py` fire LangGraph-shaped callback sequences and assert the emitted `PairedEvent.parent` for each pattern.

<sup>S1-S8 are the SDK's internal labels for the eight LangGraph multi-agent topology patterns the parent-context tracker handles. Full breakdown in `tests/test_parent_context_scenarios.py` and `tests/test_block_mode_races.py`.</sup>

## Session persistence

The first call to `adrian.init()` from a given working directory generates a UUID4 and persists it to `~/.adrian/projects/<cwd-key>/config.json`, where `<cwd-key>` is the absolute working-directory path with `/` and `\` and `:` replaced by `-` (e.g. `/home/user/myapp` → `-home-user-myapp`; `C:\Users\u\proj` → `-C-Users-u-proj`). Subsequent runs from the same directory pick up the same session_id, so the dashboard sees one continuous session per deployment instead of a fresh row per process restart.

Resolution order, highest priority first:

1. `ADRIAN_SESSION_ID` environment variable.
2. Explicit `session_id="..."` kwarg to `init()`.
3. Persisted value at `~/.adrian/projects/<cwd-key>/config.json`.
4. Generate a new UUID4 and persist it for next time.

Overrides via env var or kwarg do **not** write to the persistent file - the on-disk identifier is preserved for runs that want to fall back to it. Different working directories get distinct identifiers; multiple processes from the same cwd share one.

## Manual instrumentation

When `auto_instrument=True` (the default), the SDK monkey-patches LangChain at import time. Set `auto_instrument=False` if you would rather attach the handler explicitly to specific calls - useful when you do not want third-party libraries patched globally, or when you are integrating Adrian into code that already manages its own callbacks.

```python
import adrian
from langchain_openai import ChatOpenAI

adrian.init(api_key="adr_live_...", auto_instrument=False)
handler = adrian.get_handler()

llm = ChatOpenAI(model="gpt-4o")
await llm.ainvoke(prompt, config={"callbacks": [handler]})
```

`adrian.get_handler()` returns the handler the SDK built during `init()` and wired into the WebSocket hook chain. Constructing a fresh `AdrianCallbackHandler()` directly bypasses that wiring and emits no events, so `get_handler()` is the supported entry point.

## License

Apache 2.0. See [LICENSE](https://github.com/secureagentics/Adrian/blob/main/LICENSE).
