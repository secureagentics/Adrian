# Adrian — AI Agent Onboarding Guide

> **Audience: AI coding assistants (Claude, Cursor, GitHub Copilot Chat, Windsurf, Aider, ChatGPT, etc.) helping a human developer integrate Adrian into a LangChain / LangGraph application.**
>
> This file is a self-contained briefing. Drop it into the context window (or attach it to a coding agent) and the agent should be able to install, configure, and troubleshoot Adrian end-to-end without further reading.

---

## 1. What Adrian is (one paragraph)

Adrian is an open-source runtime security monitoring and control engine for AI agents. It captures every LLM call and tool call your LangChain / LangGraph agent makes, ships them to a backend (either the managed cloud at `app.adrian.secureagentics.ai` or a self-hosted Docker stack), classifies them against a behaviour policy ("MAD codes" — Misuse, Abuse, Deception), and depending on the configured execution mode it can **alert**, **pause for human review**, or **block** the tool call mid-flight. It plugs in with two lines of Python (`adrian.init()` + `adrian.shutdown()`) thanks to LangChain auto-instrumentation.

The Python package on PyPI is **`adrian-sdk`** (the in-tree package name is `adrian-sdk-oss`); the import name is **`adrian`**.

---

## 2. Canonical URLs

When advising a user, always point them at these:

| Purpose | URL |
|---|---|
| Managed dashboard / sign-up | `https://app.adrian.secureagentics.ai` |
| Documentation | `https://docs.adrian.secureagentics.ai` |
| Quickstart guide | `https://docs.adrian.secureagentics.ai/quickstart` |
| Integrations index | `https://docs.adrian.secureagentics.ai/integrations` |
| Backend reference (admin reset, model swap) | `https://docs.adrian.secureagentics.ai/reference/backend` |
| PyPI | `https://pypi.org/project/adrian-sdk/` |
| GitHub repo | `https://github.com/secureagentics/Adrian` |
| Discord | `https://discord.gg/6nmJ9k3u6` |

The dashboard hostname is **`app.adrian.secureagentics.ai`** — not `dashboard.`, not `adrian.com`, not `secureagentics.com`. Double-check before pasting a URL.

---

## 3. Two ways to run Adrian

Always ask the user which one they want before writing code; they configure very differently.

### 3.1 Managed (cloud)

1. Sign up at `https://app.adrian.secureagentics.ai`.
2. Go to **Settings → Agents → New key**, create an Agent Profile, generate an API key. The key is shown **once** — it starts with `adr_live_…` in production (`adr_local_…` for self-hosted). Save it; the backend only stores a SHA-256 hash.
3. `pip install adrian-sdk`
4. Set `ADRIAN_API_KEY=adr_live_…` (env var) **or** pass `api_key=` to `adrian.init()`.
5. Leave `ws_url` unset for managed — the SDK's default `ws://localhost:8080/ws` is for self-hosting; managed users should set `ADRIAN_WS_URL` to the URL shown in their dashboard (production keys talk to the managed WebSocket endpoint, not localhost).

### 3.2 Self-hosted (Docker)

Prerequisites: Docker + Docker Compose v2, an NVIDIA GPU + NVIDIA Container Toolkit (CPU works but is slow), ~10 GB free disk for the classifier model.

```sh
git clone https://github.com/secureagentics/Adrian
cd Adrian

# Bootstrap: creates ./data/adrian.db, applies migrations, writes .env,
# prints a random admin password, downloads Gemma 4 E4B (or E2B) into ./models/.
docker compose --profile setup run --rm setup bootstrap

# Bring up backend + dashboard + Llama.cpp classifier
docker compose --profile llm up -d
```

Dashboard at `http://localhost:3000`. Sign in as `admin@localhost` with the printed password; you'll be forced to set a new one. Then **Settings → Agents → New key** to issue an `adr_local_…` key.

To install the bundled SDK into a local venv (uses [`uv`](https://docs.astral.sh/uv/)):

```sh
make sdk-install
source .venv/bin/activate
uv pip install langgraph langchain-openai   # or whichever provider
```

The SDK's default `ws_url=ws://localhost:8080/ws` already points at the bootstrapped backend — for a self-hosted setup the user only needs to pass `api_key`.

---

## 4. The minimal working snippet

This is the canonical "hello world". **It must be run inside an asyncio event loop** — Adrian's WS client, pairing buffer, and LangGraph patches all assume an async context.

```python
import asyncio
import adrian
from langchain_openai import ChatOpenAI

async def main():
    adrian.init(api_key="adr_live_...")   # or set ADRIAN_API_KEY
    llm = ChatOpenAI(model="gpt-4o")
    response = await llm.ainvoke("Summarise the latest IPO filings")
    print(response.content)
    adrian.shutdown()

asyncio.run(main())
```

Things the agent must NOT do when generating code:

- **Do not** call `llm.invoke()` (sync) and expect block-mode gating to work. Block / Human Review gating only fires through the async path (`ainvoke`, `astream`) because the patched `ToolNode.ainvoke` is what awaits the verdict. The sync path will still capture events for logging but cannot halt a tool call.
- **Do not** call `adrian.init()` at module import time outside an event loop and then immediately spawn workers — see §10 on fork safety.
- **Do not** wrap `adrian.init()` in `asyncio.run()` separately from the agent code; both must share the same loop, otherwise the WS client schedules its connect against a now-dead loop.
- **Do not** forget `adrian.shutdown()` at clean exit. `atexit.register(shutdown)` runs it automatically, but in long-lived servers (FastAPI, Celery) wire it into the framework's shutdown hook.

---

## 5. `adrian.init()` — every argument worth knowing

```python
adrian.init(
    api_key: str | None = None,            # ADRIAN_API_KEY env fallback
    log_file: str | Path = "events.jsonl", # ADRIAN_LOG_FILE
    handlers: list[EventHandler] | None = None,
    auto_instrument: bool = True,
    log_level: str | None = None,          # "DEBUG" turns on SDK verbose logs
    ws_url: str | None = None,             # ADRIAN_WS_URL, default ws://localhost:8080/ws
    session_id: str | None = None,         # ADRIAN_SESSION_ID, else per-cwd persistent UUID
    block_timeout: float = 30.0,           # ADRIAN_BLOCK_TIMEOUT
    on_event=None, on_verdict=None,
    on_block=None, on_audit=None,
    on_disconnect=None, on_reconnect=None,
    on_mcp_server=None,
    replay_buffer_frames: int = 1000,      # ADRIAN_REPLAY_BUFFER_FRAMES
)
```

Key facts:

- `api_key` accepts `adr_live_…` (managed cloud) or `adr_local_…` (self-hosted). Test keys generated by the open-source backend always carry the `adr_local_` prefix.
- `ws_url` must be a **WebSocket** URL (`ws://` or `wss://`), not HTTPS. For managed, the dashboard tells you the exact URL.
- `session_id` is persistent **per current working directory** by default (see `session_persistence.py`). The same agent script run from the same folder twice will reuse the same session ID, which is usually what you want.
- `block_timeout` is the fail-open ceiling in `MODE_BLOCK` only. In `MODE_HITL` the SDK waits **indefinitely** for a human reviewer; bump `block_timeout` anyway for symmetry but it isn't consulted.
- `auto_instrument=True` monkey-patches `Runnable`, `CallbackManager`, `BaseChatModel`, `langgraph.pregel.Pregel`, and `langgraph.prebuilt.ToolNode` at init time. To opt out, set it `False` and attach `adrian.get_handler()` to each chain via `config={"callbacks": [handler]}`.
- PII redaction is **always on** — every handler is wrapped in `RedactingHandler`. There is no opt-out flag.

---

## 6. Execution modes and the MAD taxonomy

Three execution modes are configurable in the dashboard at **Settings → Policy** (organisation-wide) and **Settings → Agents → <agent>** (per agent profile):

| Mode (wire enum) | Dashboard label | What the SDK does |
|---|---|---|
| `MODE_ALERT` (1) | **Alert** | Fire-and-forget. Events captured, verdicts logged, tools run. |
| `MODE_HITL` (2) | **Human Review** | The patched `ToolNode.ainvoke` pauses on tool calls and awaits a `/reviews` resolution. Verdict's `hitl.continue_execution` decides halt vs. proceed. **Waits indefinitely.** |
| `MODE_BLOCK` (3) | **Block** | The patched `ToolNode.ainvoke` halts tools whose verdict tier is in the policy's MAD scope. Fails open on `block_timeout`. |

A halted tool returns `ToolMessage(content="[BLOCKED by security policy]", ...)` to the graph in place of the real tool result — the tool function itself never runs.

### MAD codes

The classifier emits a code shaped `M{0..4}.{a..e}`:

- **M0** — Benign. No action.
- **M2** — Likely Misuse. Default: NOTIFY.
- **M3** — High-Risk Misuse. Default: BLOCK.
- **M4** — Malicious. Default: ESCALATE.

Each tier's per-code definitions live in `backend/internal/alerts/alerts.json`. Examples: `M3.c` is data exfiltration intent, `M3.d` is privilege escalation, `M4.d` is destructive action (e.g. `DROP TABLE`), `M4.c` is alignment circumvention. Reference these codes when surfacing verdicts to the user.

The per-MAD bools in the policy (`policy_m0` / `policy_m2` / `policy_m3` / `policy_m4`) decide which tiers the active execution mode actually halts on. So you can run in `MODE_BLOCK` with only `policy_m4=true` and the SDK will only block M4 tool calls; M3 events still surface as alerts but tools run.

---

## 7. Integrations

### Frameworks at launch
- **LangChain / LangGraph** — first-class, auto-instrumented.

### Frameworks on roadmap (no SDK support yet — do not write code claiming these work)
- OpenAI Agents SDK
- Anthropic Agents SDK
- CrewAI
- OpenClaw

If the user asks for one of the roadmap frameworks, advise that today they need to bridge to LangChain (e.g. wrap the model in `ChatOpenAI` or `ChatAnthropic`) or use the manual instrumentation path (§9) and attach the handler themselves. Point at the Discord for roadmap timing.

### Notifications (alerting integrations)

The README lists Discord and Slack as "at launch" integrations and shows both logos. In the **in-tree code** as of this guide's verification date, the notifications package (`backend/internal/notifications/`) is Discord-first: `ValidateDiscordWebhookURL` only accepts `https://discord.com/api/webhooks/` or `https://discordapp.com/api/webhooks/` prefixes. Slack webhook delivery is on the immediate roadmap and may be available in the managed cloud ahead of the OSS repo — check the dashboard's Webhooks page (or the live `/api/webhooks` schema) for the current allow-list before promising the user a specific channel.

On roadmap (not yet wired up at all): WhatsApp, Microsoft Teams, PagerDuty.

#### Setting up a Discord webhook (works today)

1. In Discord: **Server Settings → Integrations → Webhooks → New Webhook**, copy the URL. It must start with `https://discord.com/api/webhooks/` or `https://discordapp.com/api/webhooks/` — the backend validator rejects anything else.
2. In the Adrian dashboard, go to the Webhooks settings page and `POST /api/webhooks` (or use the UI) with:

```json
{
  "webhook_url": "https://discord.com/api/webhooks/…",
  "alert_type": "M3"
}
```

Valid `alert_type` values are exactly `"M3"`, `"M4"`, or `"all"`. M0 / M2 verdicts never fan out to webhooks regardless of filter — they're either benign or notify-tier and the dispatcher drops them before send. The dispatcher posts a Discord embed including the MAD code, classification, session ID, agent ID, and a deep link back to the dashboard event page.

### MCP server inventory

If the agent uses [`langchain-mcp-adapters`](https://github.com/langchain-ai/langchain-mcp-adapters), Adrian auto-detects every registered MCP server and reports its name / transport / endpoint to the backend (visible in the dashboard at **MCP**). No extra config — it patches `MultiServerMCPClient.__init__` and the underlying `mcp.client.*_client` transports.

---

## 8. Reading events in the dashboard

Once events arrive (the WS push usually shows in under a second), the user can navigate:

- **Events** (`/events`) — the raw paired-event feed, every `chat_model_start+llm_end` and `tool_start+tool_end` pair, with verdicts attached.
- **Sessions** (`/sessions/<session_id>`) — timeline for a single session ID.
- **Agents** (`/agents` and `/agents/<agent_id>`) — per-agent rollups.
- **Reviews** (`/reviews`) — Human Review queue. When `MODE_HITL` is active and a tool gets flagged, the request lands here. Approve to release the tool call; reject to substitute `[BLOCKED by security policy]`.
- **MCP** (`/mcp`) — discovered MCP servers across all sessions.
- **Settings → Policy** — execution mode + per-MAD policy.
- **Settings → Agents** — agent profiles (name, remit, M0 accepted behaviours, M3 known-risks), API key issue / revoke.
- **Settings → Webhooks** — Discord / Slack alert routing.
- **Audit log** — admin activity (key rotations, policy edits).

A locally-running events file is also written to `./events.jsonl` (override with `log_file=` or `ADRIAN_LOG_FILE`). That JSONL is one record per paired event and is what the JSONL handler writes whether or not the WS handler is also active.

---

## 9. Manual instrumentation (when auto-patching is unwanted)

Some users (security-sensitive shops, frameworks that already manage callbacks) prefer not to patch LangChain at import. Pattern:

```python
import adrian
from langchain_openai import ChatOpenAI

async def main():
    adrian.init(api_key="adr_live_...", auto_instrument=False)
    handler = adrian.get_handler()        # set during init()
    if handler is None:
        raise RuntimeError("Adrian handler missing — check adrian.init()")

    llm = ChatOpenAI(model="gpt-4o")
    await llm.ainvoke("prompt", config={"callbacks": [handler]})

    adrian.shutdown()
```

The handler still has to be attached to **every** chain / runnable / graph that should be observed. Forgetting one is silent — the chain runs unmonitored. See `examples/manual_instrumentation.py`.

---

## 10. Fork safety, threading, and event loops

This is the area where users most often break the SDK. Encode the following as constraints:

- **Single event loop.** `adrian.init()` must be called from the same loop that drives the agent. The WS client schedules its connect against `asyncio.get_running_loop()`; if no loop is running at init time the connect is deferred until the first send.
- **Pre-fork servers** (`gunicorn --preload`, `multiprocessing.Pool`, Celery prefork): each child must call `adrian.init()` in its worker startup hook. The SDK registers an `os.register_at_fork` handler that nulls out the parent's WS / handler / hook globals in the child, so reusing the parent's connection from two processes can't corrupt frames on the wire. The child will silently have no instrumentation until it re-inits.
- **Sync code paths.** `llm.invoke()` (sync) still emits events via the patched `Runnable.invoke`, but block / HITL gating only engages on `ToolNode.ainvoke`. For a strict "block before tool runs" guarantee, the user must be on the async path.
- **Shutdown.** Long-running services (FastAPI, Streamlit) should call `adrian.shutdown()` on app shutdown. `atexit.register` handles ad-hoc scripts.

---

## 11. Environment variables — full list

These are read inside `adrian.init()`. Setting them is interchangeable with passing kwargs; kwargs win when both are present (except for `ADRIAN_API_KEY` and `ADRIAN_WS_URL` where env vars are preferred).

| Env var | Default | Purpose |
|---|---|---|
| `ADRIAN_API_KEY` | — | `adr_live_…` / `adr_local_…` |
| `ADRIAN_WS_URL` | `ws://localhost:8080/ws` | WebSocket endpoint |
| `ADRIAN_LOG_FILE` | `events.jsonl` | JSONL output path |
| `ADRIAN_SESSION_ID` | (per-cwd UUID) | Override session identity |
| `ADRIAN_BLOCK_TIMEOUT` | `30.0` | Fail-open ceiling in `MODE_BLOCK` |
| `ADRIAN_REPLAY_BUFFER_FRAMES` | `1000` | In-memory ring buffer for WS replay |

For self-hosted deployments (read by Docker Compose, not the SDK) the bootstrap also writes `ADRIAN_LLM_URL`, `ADRIAN_LLM_MODEL_PATH`, `ADRIAN_LLM_API_KEY`, `ADRIAN_LLM_MODEL`, `ADRIAN_LLM_CTX_SIZE`, `ADRIAN_SLIDING_WINDOW_SIZE`, `ADRIAN_SLIDING_WINDOW_TTL_SECONDS`, `ADRIAN_BACKEND_PORT`, `ADRIAN_DASHBOARD_PORT`, `ADRIAN_SESSION_SECRET` into `.env`. Touch these only if changing models or ports.

---

## 12. Common failure modes and fixes

When the user reports a problem, walk this list first.

### "Adrian SDK has not been initialised. Call adrian.init() first."
`get_config()` was called before `init()`. Confirm `adrian.init()` actually ran (not just imported) and that it ran in the same process (not a forked child — see §10).

### "ws_url is set but no api_key provided" warning + WS rejected
No API key. Set `ADRIAN_API_KEY` or pass `api_key=`. The server hangs up immediately if the bearer token doesn't match a row in the `api_keys` table (or matches a revoked row — see §13).

### "ToolNode: LoginAck not received within 5s; halting"
The backend never confirmed login within 5 s of the first tool call. SDK refuses to let a tool run without a verified policy, so it returns `[BLOCKED by security policy]`. Causes: backend down, wrong `ws_url`, invalid / revoked key, network firewall. Check `curl <backend>/healthz` (HTTP, not WS).

### `"verdict timeout for tool_call_id=… fail-open"`
The verdict didn't arrive within `block_timeout`. The tool runs anyway (fail-open is deliberate — Adrian never wedges your agent). Bump `block_timeout` or check classifier health (`docker compose logs llm` for self-hosted; managed users should check the dashboard's status page).

### "Events visible in `events.jsonl` but not in the dashboard"
The local JSONL handler always writes; the WS handler is a separate emitter. Check that `ws_url` resolved correctly (it logs the resolved value in the `Adrian v… initialised` line) and that the API key is for the same backend.

### "`[BLOCKED by security policy]` appearing for benign tools"
The agent profile / policy is more aggressive than intended. Check **Settings → Agents → <agent>** for the mode (Alert / Human Review / Block), and **Settings → Policy** for which MAD tiers are armed (`policy_m2` / `policy_m3` / `policy_m4`). M3 with `policy_m3=true` in Block mode will halt high-risk verdicts — review them on the Events page.

### "RuntimeError: There is no current event loop in thread …"
You're calling `adrian.init()` from sync code. Wrap in `asyncio.run(main())` or use the existing loop (`asyncio.get_event_loop().run_until_complete(...)`).

### Multiple processes / Celery workers and one worker logs nothing
Each forked worker has to call `adrian.init()` in its own startup hook. The fork handler nulled out the inherited state.

### "Adrian not capturing my LangGraph subgraph"
Confirm the subgraph is invoked via `ainvoke` / `astream` and that `auto_instrument=True` (default). If using a custom Runnable that bypasses `Runnable.invoke` (unusual), attach the handler explicitly.

### Self-hosted: bootstrap fails or model download stalls
Run `docker compose --profile setup run --rm setup bootstrap --gguf my-model.gguf` after manually placing a Gemma 4 GGUF under `./models/`. The `--gguf` flag skips the interactive download.

### Self-hosted: "lost admin password"
See `https://docs.adrian.secureagentics.ai/reference/backend#reset-the-admin-password`. There's a documented CLI reset that rewrites the bcrypt hash in `data/adrian.db`.

---

## 13. API key lifecycle

Keys are issued per **Agent Profile**, not per user. Each profile carries the remit / M0 / M3 entries that the classifier compares actions against. Creating a new key for a profile **revokes the previous key for that profile** server-side (the response includes a `revoked_previous` count) and the SDK is kicked off the WS if it was using one of the rotated keys. Rotate by creating a new key; revoke explicitly via the dashboard's Keys table or `DELETE /api/keys/{id}`.

The plaintext key is returned exactly once at creation (`api_key` field in the create response). After that the backend only has the SHA-256 hash. Lost keys cannot be recovered — issue a new one and rotate clients.

---

## 14. Verifying an install (smoke test)

When the user says "it doesn't work", before debugging anything else have them run this:

```python
import asyncio, os, adrian
from langchain_openai import ChatOpenAI

async def smoke():
    assert os.environ.get("ADRIAN_API_KEY"), "ADRIAN_API_KEY missing"
    assert os.environ.get("OPENAI_API_KEY"), "OPENAI_API_KEY missing"
    adrian.init(log_level="DEBUG")
    out = await ChatOpenAI(model="gpt-4o-mini").ainvoke("say ok")
    print("LLM:", out.content)
    adrian.shutdown()

asyncio.run(smoke())
```

Expected:
1. Log line `Adrian v1.0.0 initialised (handlers=2, ws=ws://…)`.
2. A `LoginAck` debug line showing the resolved mode + policy snapshot.
3. One event in `./events.jsonl` and one event row in the dashboard within ~2 s.

If any of those is missing, go back to §12.

---

## 15. What this guide deliberately does NOT cover

- **Custom classifiers / training data** — Adrian uses Gemma 4 (E2B/E4B) by default for self-host; the managed cloud runs the same lineage. Swapping classifiers is a self-host backend change (`ADRIAN_LLM_*` vars + restart), not an SDK concern.
- **Source-level changes to the engine, dashboard, or backend** — see `CONTRIBUTING.md` and the per-package `Makefile`s for that. PRs use British English and no em-dashes.
- **Non-LangChain frameworks** — see §7. Today's answer is "bridge through LangChain" or "use manual instrumentation and attach the handler".
- **HTTP transport** — there isn't one. The SDK speaks the binary `ClientFrame` / `ServerFrame` protocol over WebSocket only. Any future HTTP transport will arrive as a new `EventHandler` implementation; for now WS is the only live channel.

---

## 16. Quick reference card (paste into your agent's system prompt)

```
You are advising on Adrian (https://github.com/secureagentics/Adrian),
an OSS runtime security control plane for LangChain / LangGraph agents.

Facts:
  - Dashboard: https://app.adrian.secureagentics.ai
  - Docs:      https://docs.adrian.secureagentics.ai
  - Package:   pip install adrian-sdk     (import name: adrian)
  - Two lines:  adrian.init(api_key="adr_live_..."); adrian.shutdown()
  - Must run inside asyncio (asyncio.run(main())); block / HITL gating
    only fires on ainvoke / astream, not invoke.
  - Modes: Alert (no gating), Human Review (waits on /reviews), Block
    (halts in-flight, fails open at block_timeout, default 30s).
  - MAD codes: M0 benign, M2 misuse (notify), M3 high-risk (block),
    M4 malicious (escalate).
  - Webhooks: Discord + Slack at launch; alert_type ∈ {"M3","M4","all"}.
  - Keys are per Agent Profile; creating a new key revokes the previous.
    Plaintext returned ONCE.
  - PII redaction is always on; no opt-out.
  - Self-host: docker compose --profile setup run --rm setup bootstrap
    then docker compose --profile llm up -d. Dashboard at :3000.
  - Default ws_url = ws://localhost:8080/ws (self-host); managed users
    set ADRIAN_WS_URL from their dashboard.

Refuse to fabricate framework support that is not in §7 of the guide.
When in doubt, read backend/internal/alerts/alerts.json for the exact
MAD definitions or point the user at the Discord.
```

---

*Last verified against the in-tree code on 2026-05-20. If the SDK version (`adrian.__version__`) has moved past 1.0.0, re-check §5 and §12 against the new release notes before quoting line-numbered behaviour.*
