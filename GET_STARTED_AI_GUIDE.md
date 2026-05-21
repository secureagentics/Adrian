# Adrian — AI Agent Setup Guide

> **For human developers:** drop this file into your AI assistant's context (Claude, Codex, Cursor, Aider, Windsurf, Copilot Chat, ChatGPT, etc.) and ask it to set up Adrian. The agent walks you through a 60-second flow: API key → install → first event in the dashboard. Everything below **Part 1** is reference material the agent reads when something goes wrong.
>
> **For AI agents:** if a user has just pointed you at this file (or at `github.com/secureagentics/Adrian`) and asked for help setting up, execute **Part 1** below in order. Don't skim. Don't suggest a different flow. The single success criterion is "first event visible in the dashboard within 60 seconds". Treat Part 2 as a lookup index for failure modes.

---

# PART 1 — 60-Second Setup Flow (executable)

> **AI agent: this section is a program, not documentation. Run it top-to-bottom. Branch only at the marked decision points. If a step fails, jump to the matching subsection in Part 2 and resume.**

## Step 0 — Confirm preconditions (5 seconds)

Run these checks silently (don't ask the user to type them). Either you can shell out yourself, or you ask the user to confirm.

| Requirement | How to check |
|---|---|
| Python ≥ 3.12 | `python3 --version` |
| pip available | `python3 -m pip --version` |
| A working directory | `pwd` (any folder; we'll write one or two files here) |
| Network access to PyPI + `app.adrian.secureagentics.ai` | implicit; flag if obviously blocked |

If Python < 3.12, stop and ask the user to install 3.12 (or use `pyenv install 3.12`). Don't proceed — the SDK declares `requires-python = ">=3.12"` and will fail to install.

## Step 1 — Get the user's Adrian API key (15 seconds)

This step has three substeps. Don't skip the verification at the end — Steps 3 and 4 assume you've already confirmed a valid key is in place.

### 1.1 Walk the user through generating the key

Tell the user this, click-by-click. Don't paraphrase — the user is on a 60-second clock and a sentence-long "go get a key" leaves them hunting menus.

> 1. Open **https://app.adrian.secureagentics.ai** in your browser.
> 2. Click **Sign up** (top right) and choose Google, Microsoft, or GitHub SSO. About 30 seconds.
> 3. Once you're in the dashboard, go to **Settings → Agents** (left sidebar).
> 4. Click **New key**. Give it any label (e.g. "60-second setup") and click **Generate**.
> 5. Copy the key. It starts with `adr_live_…`. The dashboard shows it **once** — if you close the modal without copying, you'll need to issue a new one.
> 6. Tell me when you have it.

Wait for the user to indicate they have the key. Don't proceed without that confirmation.

### 1.2 Recommend the secure path (.env file), and offer to accept a paste as a fallback

Default flow — what you should ask for first:

> Recommended: create a file called `.env` in your project directory with this one line, then save it:
>
> ```
> ADRIAN_API_KEY=adr_live_…
> ```
>
> (Replace `adr_live_…` with the key you just copied.) When you've saved the file, type **done** and I'll verify it.

If the user follows this path → go to 1.3 ("verification of .env").

If the user pastes the key directly into chat instead (this happens often) → accept it gracefully. Don't refuse. But switch behaviour:

- **Do not try to silently write it to `.env`.** Many agents can't write arbitrary files (Codex web, ChatGPT, Cursor in restricted mode), and even agents that can may not survive a context reload to remember the file path. The user's shell may also have already loaded a stale `.env`. Trying to "help" here usually creates a confusing mismatch between what's in the file and what the running shell sees.
- **Instead, plan to hardcode the key into the script you write in Step 3.** When you reach Step 3A, change `adrian.init()` to `adrian.init(api_key="adr_live_…")` with the actual key. For Step 3B, do the same patch in their file.
- **Warn the user explicitly, in the message where you confirm receipt of the key:**
  > Got it. I'm going to put this key directly into the code I write so it runs straight away. Before you commit that file to git or share it with anyone, replace the hardcoded key with `os.environ["ADRIAN_API_KEY"]` and move the actual value into a `.env` file that's gitignored. I'll mark the line with a `# TODO` comment so you can find it.

Go to 1.3 ("verification of paste").

**One rule that applies to both paths:** never echo the full key back in any later message. Refer to it as `adr_live_…` from here on. If you need to mention it in a log line, redact past the prefix.

### 1.3 Verify before moving on

This is a hard gate. Step 2 starts only after one of the following is true.

**If the user said "done" (the .env path):**

1. Confirm `.env` exists in the current working directory. If your client can read files, read it and check. If not, ask the user:
   > Quick check — can you run `cat .env` (or `type .env` on Windows) and paste the output? I want to make sure the key landed correctly.
2. Confirm the line `ADRIAN_API_KEY=…` is present and the value:
   - Starts with `adr_live_` (managed cloud) or `adr_local_` (self-hosted).
   - Has no surrounding quotes — `ADRIAN_API_KEY="adr_live_xxx"` works in most shells but tripped some users; if you see quotes, ask the user to remove them.
   - Has no surrounding whitespace — a trailing space is a common paste artifact and will fail the auth check silently later.
3. If the format is wrong, point at the exact issue and ask the user to fix it. Most common: the key was truncated during copy (Discord and Slack sometimes trim long strings on send-from-mobile).
4. If everything looks good, tell the user:
   > Key verified in `.env`. I'll read it from the environment in the code I'm about to write.

**If the user pasted the key (the chat-paste path):**

1. Confirm the pasted value matches `^adr_(live|local)_[0-9a-f]+$`. If not, ask the user to re-copy from the dashboard — they probably grabbed the prefix or a nearby string by mistake.
2. Store the key in your working memory for this session only. **Do not write it to disk yet** — you'll embed it in the script in Step 3.
3. Tell the user (do not include the key value):
   > Key received. Format looks valid. Moving on — I'll embed it into the script with a `# TODO: move to .env` marker.

Once either verification passes, set an internal flag the rest of this flow checks:

- `key_source = "env"` → in Steps 3A and 3B, leave `adrian.init()` argument-less and rely on `ADRIAN_API_KEY` from the environment.
- `key_source = "paste"` → in Steps 3A and 3B, generate `adrian.init(api_key="adr_live_…")` with the literal value and add a `# TODO: move to a .env file before committing` comment above the line.

## Step 2 — Decision point: test agent or own agent? (5 seconds)

Ask the user this question. Use whichever tool you have:

- **If you have a structured question tool** (e.g. Claude's `AskUserQuestion`): use it with the two options below.
- **If you don't** (Codex, Aider, plain chat): ask in chat and wait for "a" or "b" or the option name.

> Two ways to see Adrian in action:
>
> **(A) Test agent** — I write a tiny LangChain script, run it, and you watch the event appear in the dashboard. Fastest. Needs an `OPENAI_API_KEY` (or I can use a fake LLM if you don't have one).
>
> **(B) Your agent** — Tell me the path to your existing LangChain / LangGraph file and I add two lines so Adrian instruments it. Then run your agent as you normally would.

Branch on the answer.

## Step 3A — Test agent path (35 seconds)

**Substep 3A.1 — OpenAI key check.** Ask: "Do you have an `OPENAI_API_KEY`?" Three branches:

- **Yes, in env** → continue to 3A.2.
- **Yes, has the value** → write it into the same `.env` (`OPENAI_API_KEY=sk-…`) and continue.
- **No** → use the FakeChatModel script at the end of this section instead of the OpenAI one. Continue to 3A.2.

**Substep 3A.2 — Create venv and install.** Run, in the user's working directory:

```sh
python3 -m venv .venv
source .venv/bin/activate            # on Windows: .venv\Scripts\activate
pip install adrian-sdk langchain-openai
```

If `pip install` exits non-zero, jump to Part 2 §12.

**Substep 3A.3 — Write the smoke-test script.** Create `adrian_quickstart.py` in the working directory. **The exact code depends on the `key_source` you set in Step 1.3** — pick the matching variant.

#### Variant A — `key_source = "env"` (user saved the key to `.env`)

```python
"""Adrian 60-second smoke test."""
import asyncio
import os
import sys
import adrian
from langchain_openai import ChatOpenAI


async def main() -> int:
    if not os.environ.get("ADRIAN_API_KEY"):
        sys.exit("ADRIAN_API_KEY missing. Did you source your .env? "
                 "Try: set -a; . ./.env; set +a")
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY missing. Set it in your shell or .env.")

    adrian.init()  # reads ADRIAN_API_KEY from env automatically
    llm = ChatOpenAI(model="gpt-4o-mini")
    response = await llm.ainvoke("In one sentence, why is the sky blue?")
    print("LLM said:", response.content)
    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

#### Variant B — `key_source = "paste"` (user pasted the key into chat)

Substitute the literal key the user pasted in place of `adr_live_REPLACE_ME` on the marked line. **Keep the TODO comment** — the user explicitly opted into the convenience trade-off and the comment is the audit trail.

```python
"""Adrian 60-second smoke test."""
import asyncio
import os
import sys
import adrian
from langchain_openai import ChatOpenAI


async def main() -> int:
    # TODO: move this key to a .env file before committing this script.
    # Replace the literal with os.environ["ADRIAN_API_KEY"] and put
    # ADRIAN_API_KEY=adr_live_... in .env (which should be gitignored).
    ADRIAN_API_KEY = "adr_live_REPLACE_ME"

    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY missing. Set it in your shell or .env.")

    adrian.init(api_key=ADRIAN_API_KEY)
    llm = ChatOpenAI(model="gpt-4o-mini")
    response = await llm.ainvoke("In one sentence, why is the sky blue?")
    print("LLM said:", response.content)
    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

#### FakeChatModel fallback (no OpenAI key)

Use this *instead* of the variant above if the user has no `OPENAI_API_KEY`. Pick the env / paste flavour to match `key_source` (only the `adrian.init` line differs):

```python
"""Adrian 60-second smoke test (no LLM provider needed)."""
import asyncio
import os
import sys
import adrian
from langchain_core.language_models.fake_chat_models import FakeListChatModel


async def main() -> int:
    # env variant:
    adrian.init()
    # paste variant (replace with the literal key):
    # adrian.init(api_key="adr_live_REPLACE_ME")  # TODO: move to .env

    llm = FakeListChatModel(responses=["The sky is blue because of Rayleigh scattering."])
    out = await llm.ainvoke("In one sentence, why is the sky blue?")
    print("Fake LLM said:", out.content)
    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

**Substep 3A.4 — Run it.** Run from the same shell that has the venv active. The command depends on `key_source`:

- **`key_source = "env"`** — source `.env` first so `ADRIAN_API_KEY` (and `OPENAI_API_KEY`, if applicable) are in the process environment:
  ```sh
  set -a; . ./.env; set +a
  python adrian_quickstart.py
  ```
- **`key_source = "paste"`** — the Adrian key is already inlined in the script. You still need `OPENAI_API_KEY` in the shell (unless you used the FakeChatModel variant):
  ```sh
  export OPENAI_API_KEY=sk-...   # skip if using FakeChatModel
  python adrian_quickstart.py
  ```

Expected output: a log line `Adrian v1.0.0 initialised (handlers=2, ws=ws://localhost:8080/ws)` (or the managed cloud's WS URL once configured), followed by the LLM's response. If you see `Adrian v…` you've already won — the event is on its way.

If the WS URL still says `ws://localhost:8080/ws` and the user is on managed (not self-hosted), they need `ADRIAN_WS_URL` set to the URL the dashboard tells them to use. Add it to `.env` and re-run. Don't make this a blocker for the smoke test — the local JSONL handler is still writing `events.jsonl` next to the script so the user has something tangible immediately.

**Substep 3A.5 — Direct to dashboard.** Tell the user:

> Open **https://app.adrian.secureagentics.ai/events** — your event should be there within 2-3 seconds, classified as benign (M0).

Then go to Step 4.

## Step 3B — Own agent path (35 seconds)

**Substep 3B.1 — Get the file.** Ask:

> What's the path to the LangChain or LangGraph agent file you want to instrument? (Absolute path, or relative to the current directory.)

Read the file. If it's a directory, ask which file inside. If it's not Python, stop and explain Adrian's SDK is Python-only (LangChain/LangGraph).

**Substep 3B.2 — Validate the file.** Check three things:

1. Does it import from `langchain_*` or `langgraph`? If neither, this file isn't a LangChain agent — stop and tell the user.
2. Does it have an `async def` somewhere (the agent entry)? Look for the function that calls `await ...ainvoke(...)` or `await ...astream(...)`.
3. Does it use **sync** `.invoke()` instead of `.ainvoke()`? If yes, warn:
   > Heads-up: your agent uses the sync `.invoke()` path. Adrian will still capture events for logging, but Block / Human Review gating only fires on the async path (`ainvoke` / `astream`). If you want in-flight tool blocking later, you'll need to convert to async.
   Continue anyway — capture works either way.

**Substep 3B.3 — Identify the patch site.** The patch is two insertions, and the second one differs based on the `key_source` flag set in Step 1.3.

1. **Imports block at the top of the file:** add `import adrian`. Add `import os` too if `os` isn't already imported (only needed for the env variant below).

2. **Entry function — first statement inside the `async def`.** Find the function the user runs as the agent's entry point. This is usually the `async def main():` (or similar) that's called from `asyncio.run(main())`. Insert one of the two variants below as the first statement inside that function.

   #### Variant A — `key_source = "env"` (user has `.env` set up)

   ```python
       adrian.init(api_key=os.environ["ADRIAN_API_KEY"])
   ```

   The user is responsible for sourcing `.env` before running their agent. If they already use `python-dotenv` or `direnv`, this just works. If they don't, mention it: "Source your `.env` before running with `set -a; . ./.env; set +a`."

   #### Variant B — `key_source = "paste"` (user pasted the key directly)

   ```python
       # TODO: move this key to a .env file before committing this script.
       # Replace the literal below with os.environ["ADRIAN_API_KEY"] and put
       # ADRIAN_API_KEY=adr_live_... in .env (gitignored).
       adrian.init(api_key="adr_live_REPLACE_ME")
   ```

   Substitute `adr_live_REPLACE_ME` with the actual key the user pasted. **Keep the comment block** — the user opted into the convenience trade-off and the comment makes the eventual cleanup trivial to find with `grep -rn 'TODO.*\.env'`.

You do **not** need to add `adrian.shutdown()` — the SDK registers it via `atexit` automatically. (You can still add it before a clean `return` if the agent runs forever and you want explicit teardown.)

**Sync-only agents:** if there's no `async def` and the agent is fully sync, put the same `adrian.init(...)` line once at module import time (after the imports block, before any LangChain object is constructed). Pick the env or paste variant the same way. Sync mode still captures events; it just doesn't gate tool calls.

**Substep 3B.4 — Show the diff before applying.** Print the patched file as a unified diff and ask the user to confirm before writing. (Skip this step if your client doesn't support arbitrary file writes — paste the patched code and tell the user to save it.)

**Substep 3B.5 — Install the SDK in the user's existing environment.** Detect what they're using:

- Plain venv → `pip install adrian-sdk`
- Poetry → `poetry add adrian-sdk`
- uv → `uv add adrian-sdk` (or `uv pip install adrian-sdk` in legacy projects)
- Conda → `pip install adrian-sdk` inside the activated env
- Requirements file → append `adrian-sdk` to `requirements.txt`, run `pip install -r requirements.txt`

If you can't tell, ask the user once: "What package manager does this project use?"

**Substep 3B.6 — Run their agent.** Tell the user:

> Now run your agent as you normally would. Adrian's instrumentation captures every LLM call and tool call automatically — you don't need to change how you invoke the agent.

Then go to Step 4.

## Step 4 — Verify in the dashboard (5 seconds)

Tell the user:

> Open **https://app.adrian.secureagentics.ai/events**. Within a couple of seconds you should see one or more events listed — each is one LLM call or tool call your agent made, with a classification badge (M0 benign, M2 misuse, M3 high-risk, M4 malicious). Most first runs are all M0.

If nothing shows up in 10 seconds, jump to Part 2 §12 ("Common failure modes") and walk the user through the relevant entry.

## Step 5 — Offer the next obvious step (10 seconds)

After the user confirms they see events, offer one of these as a natural next step (don't force it; just one sentence each):

- **"Want Adrian to actually block dangerous tool calls?"** → flip the agent profile to Block mode (Settings → Agents). Requires the async path.
- **"Want a Discord alert when a tool call gets flagged?"** → Settings → Webhooks → New, paste a Discord webhook URL, choose alert type `M3` / `M4` / `all`.
- **"Want to tell Adrian what your agent is supposed to do (its remit), so misuse classification is more accurate?"** → Settings → Agents → edit the profile, fill in remit + expected behaviours + known risks.

Then stop. Setup is done. The rest of this file is reference for when something goes wrong.

---

## Cross-client compatibility notes

| Agent | Multi-choice tool | File write | Shell execution | Special quirks |
|---|---|---|---|---|
| **Claude Code** (CLI) | none — ask in chat | Edit/Write tools | Bash tool | Sync via Edit; preserves diffs cleanly |
| **Claude Desktop** (Cowork) | `AskUserQuestion` | Edit/Write tools | sandboxed bash | Prefer `.env` for the API key — chat persists |
| **Codex CLI** | none — ask in chat | direct edit | shell exec | Codex defaults to streaming edits; show diff first |
| **Codex web / ChatGPT** | none — ask in chat | offer code blocks | none | Tell the user to save / run; you can't execute |
| **Cursor** chat | none — ask in chat | Composer can write | terminal panel | Composer multi-file edits work for own-agent path |
| **Aider** | none — ask in chat | yes (its model) | shell exec | Map files into the session before patching |
| **Windsurf / Copilot Chat** | none — ask in chat | varies | varies | Treat as Codex-equivalent |

If your client supports it, prefer:
- A structured multi-choice question over a free-text "type a or b". Reduces parse errors.
- Showing a diff before writing patched code (own-agent path) over a silent write. Users want a chance to bail.
- Reading `.env` once and not echoing the key back over re-reading it in every step. Reduces leak surface.

---

# PART 2 — Reference (lookup index)

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
