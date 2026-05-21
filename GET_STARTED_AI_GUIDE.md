# Adrian — AI Agent Setup Guide

> **For human developers:** drop this file into your AI assistant's context (Claude, Codex, Cursor, Aider, Windsurf, Copilot Chat, ChatGPT, etc.) and ask it to set up Adrian. The agent walks you through a 60-second flow: API key → install → first event in the dashboard. Everything below **Part 1** is reference material the agent reads when something goes wrong.
>
> **For AI agents:** if a user has just pointed you at this file (or at `github.com/secureagentics/Adrian`) and asked for help setting up, execute **Part 1** below in order. Don't skim. Don't suggest a different flow. The single success criterion is "first event visible in the dashboard within 60 seconds". Treat Part 2 as a lookup index for failure modes.

---

# PART 1 — 60-Second Setup Flow (executable)

> **AI agent: this section is a program, not documentation. Run it top-to-bottom. Branch only at the marked decision points. If a step fails, jump to the matching subsection in Part 2 and resume.**

### Ground rules for the AI agent (read once, apply throughout)

These rules override your normal defaults for this flow. Breaking any of them is the difference between a 60-second success and a frustrated user who has to redo the setup.

1. **Cross-confirm every decision with the user. Never silently pick a default.** Every time the flow branches — LLM provider, where to store an API key, what to do after the smoke test, whether to instrument an existing project — surface the choices to the user and wait for their answer. Do not assume OpenAI, do not assume `.env`, do not assume "yes, keep going". Use a structured multi-choice prompt if your client supports one (`AskUserQuestion` on Claude, otherwise list the options in chat and wait).
2. **Do not offer or use a fake / mock / stub LLM under any circumstance.** A fake LLM does not produce real tool calls or model events, which means Adrian has nothing meaningful to capture, which means the smoke test "succeeds" while teaching the user nothing about how Adrian works. If the user has no LLM provider credentials at all, stop and help them get one (link to OpenAI / Anthropic / Google / Bedrock / Ollama install) before proceeding. Do not invent a path around this.
3. **Be LLM-agnostic.** Adrian instruments anything that runs through LangChain / LangGraph. Ask the user which provider they want to use rather than assuming. Supported provider integrations include (non-exhaustive): OpenAI, Anthropic, Google (Gemini / Vertex), AWS Bedrock, Azure OpenAI, Ollama (local models), Groq, Mistral, Together, Fireworks, Cohere, HuggingFace endpoints. If the user names something not on this list, ask for the LangChain package name (`langchain-<provider>`) — most providers have one.
4. **Apply the same storage preference to every secret in the flow.** The decision the user made about the Adrian API key in Step 1.2 (`key_source ∈ {"env", "inline_user", "inline_agent"}`) governs how you treat their LLM provider key in Step 3 too. Do not mix paths — that is what produces hardcoded keys sitting next to a half-populated `.env`.
5. **Explain code before you ask the user to run it.** Every script or patched file you produce must be preceded by a one-paragraph description in plain English of what it will do (which model it will call, what prompt it will send, what side effects it has, what it writes to disk). Users should never paste-and-run code they don't understand the shape of.
6. **At every named "Step N" boundary, summarise what just happened and ask if it's OK to proceed.** Especially after the smoke test runs — do not auto-jump to "configure webhooks now". Present the structured options in Step 5 and let the user pick.

## Step 0 — Confirm preconditions and establish the working directory (5 seconds)

Run these checks. Either shell out yourself or ask the user to run the commands and paste output.

| Requirement | How to check |
|---|---|
| Python ≥ 3.12 | `python3 --version` |
| pip available | `python3 -m pip --version` |
| Absolute working directory path | `pwd` (macOS / Linux / Git Bash / PowerShell), `cd` (Windows cmd) |
| Network access to PyPI + `app.adrian.secureagentics.ai` | implicit; flag if obviously blocked |

If Python < 3.12, stop and ask the user to install 3.12 (or use `pyenv install 3.12`). Don't proceed — the SDK declares `requires-python = ">=3.12"` and will fail to install.

**Then — before moving to Step 1 — announce the absolute working directory back to the user, and store it for the rest of the flow.** This is the single most common source of confusion later (the `.env` file ends up in a different folder than the user expected). Get it straight before any files exist.

> Your working directory is `/Users/you/myproject`. Every file we create — `.env`, `adrian_quickstart.py`, the virtual env — will live here. Sound right? If you wanted to be in a different folder, `cd` there now and let me know the new path before we continue.

Wait for confirmation, or for the user to give you a different path. Then store this value as an internal variable `working_dir = "<absolute path>"` and reference it whenever you generate a file-creation command in later steps. Never tell the user to "create a file in your project directory" without naming the actual absolute path.

## Step 1 — Get the user's Adrian API key (15 seconds)

This step has three substeps. Don't skip the verification at the end — Steps 3 and 4 assume you've already confirmed a valid key is in place.

### 1.1 Walk the user through generating the key

Tell the user this, click-by-click. Don't paraphrase — the user is on a 60-second clock and a sentence-long "go get a key" leaves them hunting menus.

> 1. Open **https://app.adrian.secureagentics.ai** in your browser.
> 2. Click **Sign up** (top right) and choose Google, Microsoft, or GitHub SSO. About 30 seconds.
> 3. Once you're in the dashboard, go to **Settings → Agents** (left sidebar).
> 4. Click **New key**. Give it any label (e.g. "60-second setup") and click **Generate**.
> 5. Copy the key. It starts with `adr_live_…`. The dashboard shows it **once** — if you close the modal without copying, you'll need to issue a new one.
> 6. Keep the key on your clipboard — I'll ask you next where you want to store it.

Don't pause for a separate "do you have it?" confirmation. Go straight to 1.2; the user's answer to the storage question in 1.2 is also the signal that they have the key.

### 1.2 Ask the user how they want to store the key (upfront 3-option choice)

Don't pick a storage path on the user's behalf — present all three options at once and let them pick. Use a structured multi-choice prompt if your client supports one (`AskUserQuestion` on Claude); otherwise list them in chat numbered 1 / 2 / 3 and wait for the user's pick.

> You've got your Adrian API key. Where would you like to store it? Pick one — same answer will apply to any other API keys we need later (e.g. for an LLM provider) so we stay consistent.
>
> **1. Save it to a `.env` file in your project directory (recommended).** Most flexible and most secure: the script reads the key from the environment, the file never goes to git, and you can rotate the key by editing one line. The key never passes through this chat. Best default for almost everyone.
>
> **2. Add it to the code yourself.** I'll write the script with a `PASTE_YOUR_KEY_HERE` placeholder; you replace it with your real key in your editor before running. The key never passes through this chat. Pick this if you don't want your key transiting the chat context (e.g. on Codex web / ChatGPT) or if you just prefer to handle secrets manually.
>
> **3. Paste it to me in chat and I'll embed it in the script.** Fastest path; everything runs in one shot. The key will sit in this chat history. I'll add a `# TODO: move to .env` comment so future-you can clean it up later. Pick this only if you trust this chat with your key.

Wait for the user's answer. Set the internal flag `key_source` and proceed to 1.3:

- Option **1** → `key_source = "env"`
- Option **2** → `key_source = "inline_user"` (you'll leave a placeholder in the script; the user replaces it in their editor)
- Option **3** → `key_source = "inline_agent"` (the user pastes the key to you in chat; you embed it literally in the script)

**A few rules that apply to every option:**

- Never echo the full key back in any later message. Refer to it as `adr_live_…` from here on. If you need to mention it in a log line, redact past the prefix.
- The same option will govern any other secrets in this flow (e.g. the LLM provider key in Step 3A). Don't mix paths — if Adrian's key went into `.env`, the provider key goes into the same `.env`; if Adrian's key is inline in the script, the provider key is inline the same way.
- If the user picks option 2, do **not** also ask them to paste the key — the whole point of option 2 is that the key never enters this chat.
- If the user picks option 3, you don't need a separate "warn before inlining" step — by picking option 3 they've already consented. Just confirm receipt of the key (without echoing it) and move on.

### 1.3 Verify before moving on

This is a hard gate. Step 2 starts only after the verification path matching the user's option in 1.2 passes.

**If `key_source = "env"` (option 1, `.env` file):**

Give the user a single copy-pasteable command that creates the `.env` file at the **absolute path** you stored in Step 0 as `working_dir`, then opens it for editing. Don't tell them to "create a file in your project directory" — that's the wording that produced the file-in-the-wrong-place failure in early demos. Use the absolute path and pick the command for their OS.

Example wording (substitute `working_dir` with the actual absolute path, e.g. `/Users/you/myproject`):

> Run **one** of these in your terminal — whichever matches your OS. The path is the absolute path to your working directory, so the file ends up exactly where the script will look for it later.
>
> **macOS:**
> ```sh
> touch "/Users/you/myproject/.env" && open -t "/Users/you/myproject/.env"
> ```
>
> **Linux (any editor):**
> ```sh
> touch "/Users/you/myproject/.env" && "${EDITOR:-nano}" "/Users/you/myproject/.env"
> ```
>
> **Windows PowerShell:**
> ```powershell
> New-Item -ItemType File -Force "C:\Users\you\myproject\.env"; notepad "C:\Users\you\myproject\.env"
> ```
>
> **Windows cmd:**
> ```cmd
> type nul > "C:\Users\you\myproject\.env" && notepad "C:\Users\you\myproject\.env"
> ```
>
> When the editor opens, paste this single line (with your actual key substituted for `adr_live_…`), save the file, and close the editor:
>
> ```
> ADRIAN_API_KEY=adr_live_…
> ```
>
> Type **done** when the file is saved.

When they confirm, verify:

1. Confirm `.env` exists at `working_dir`. If your client can read files, read `<working_dir>/.env` and check. If not, ask the user:
   > Quick check — can you run `cat "<working_dir>/.env"` (or `type "<working_dir>\.env"` on Windows) and paste the output? I want to make sure the key landed at the right path.
2. Confirm the line `ADRIAN_API_KEY=…` is present and the value:
   - Starts with `adr_live_` (managed cloud) or `adr_local_` (self-hosted).
   - Has no surrounding quotes — `ADRIAN_API_KEY="adr_live_xxx"` works in most shells but tripped some users; if you see quotes, ask the user to remove them.
   - Has no surrounding whitespace — a trailing space is a common paste artifact and will fail the auth check silently later.
3. If the format is wrong, point at the exact issue and ask the user to fix it. Most common: the key was truncated during copy (Discord and Slack sometimes trim long strings on send-from-mobile).
4. If `.env` is not at `working_dir` but exists somewhere else on the user's machine (e.g. the user's home directory or the Adrian repo), do **not** silently accept it — that's the bug we're guarding against. Tell the user the path mismatch explicitly and re-run the create command at `working_dir`.
5. If everything looks good, tell the user:
   > Key verified in `<working_dir>/.env`. I'll read it from the environment in the code I'm about to write.

**If `key_source = "inline_user"` (option 2, user will edit the script):**

There's no value for you to verify yet — the user will paste the key into the script file in Step 3A.3 (or into their own agent file in Step 3B.3), where you'll leave a `adr_live_PASTE_YOUR_KEY_HERE` placeholder for them.

Just confirm the plan back to the user:

> Got it. I'll write the script with a `PASTE_YOUR_KEY_HERE` placeholder. Before running it, you'll replace that placeholder with your actual key in your editor. I'll remind you again at the right moment.

Then proceed to Step 2. Set a reminder for yourself to:
- Use `adr_live_PASTE_YOUR_KEY_HERE` as the literal in any generated code.
- Pause before Step 3A.4 / 3B.6 (the "run it" step) to remind the user to fill in the placeholder.

**If `key_source = "inline_agent"` (option 3, user pastes key to you):**

Ask the user to paste the key now. Then:

1. Confirm the pasted value matches `^adr_(live|local)_[0-9a-f]+$`. If not, ask the user to re-copy from the dashboard — they probably grabbed the prefix or a nearby string by mistake.
2. Store the key in your working memory for this session only. **Do not write it to disk yet** — you'll embed it in the script in Step 3.
3. Tell the user (do not include the key value):
   > Key received. Format looks valid. Moving on — I'll embed it into the script with a `# TODO: move to .env` marker.

**Summary of the flag values the rest of this flow reads:**

- `key_source = "env"` → in Steps 3A and 3B, leave `adrian.init()` argument-less and rely on `ADRIAN_API_KEY` from the environment.
- `key_source = "inline_user"` → in Steps 3A and 3B, generate `adrian.init(api_key="adr_live_PASTE_YOUR_KEY_HERE")` with a placeholder and a `# TODO: replace placeholder, then move to a .env file before committing` comment above the line. Remind the user to fill the placeholder before running.
- `key_source = "inline_agent"` → in Steps 3A and 3B, generate `adrian.init(api_key="adr_live_…")` with the literal value the user pasted, plus the same `# TODO: move to a .env file before committing` comment.

The two `inline_*` paths produce structurally identical scripts — the only difference is whether you fill in the literal key (`inline_agent`) or leave a placeholder for the user to fill in (`inline_user`).

## Step 2 — Decision point: test agent or own agent? (5 seconds)

Ask the user this question. Use whichever tool you have:

- **If you have a structured question tool** (e.g. Claude's `AskUserQuestion`): use it with the two options below.
- **If you don't** (Codex, Aider, plain chat): ask in chat and wait for "a" or "b" or the option name.

> Two ways to see Adrian in action:
>
> **(A) Test agent** — I write a tiny LangChain script that calls a real LLM once and prints the response. You watch the event show up in the dashboard. Fastest path. **Requires real credentials for some LLM provider** — OpenAI, Anthropic, Google (Gemini / Vertex), AWS Bedrock, Azure OpenAI, Ollama running locally, Groq, Mistral, or any other provider with a `langchain-…` integration. I'll ask which one you'd like to use.
>
> **(B) Your agent** — Tell me the path to your existing LangChain / LangGraph file and I add two lines so Adrian instruments it. Then run your agent as you normally would.

If the user picks (A) but does not currently have credentials for any LLM provider, **stop and help them get one** before continuing — do not substitute a fake / mock / stub LLM. A fake LLM produces no real model events for Adrian to capture, so the smoke test would silently teach the user the wrong mental model. Point them at the cheapest viable option for their situation: Ollama for "I don't want to pay anything", OpenAI / Anthropic free tiers for "I'll sign up now", AWS Bedrock for "I already use AWS", etc. Then resume Step 3A once they have a key.

Branch on the answer.

## Step 3A — Test agent path (35 seconds)

**Substep 3A.1 — Ask the user which LLM provider to use.** Do not pick one for them. Use a structured multi-choice prompt if your client supports one (`AskUserQuestion` for Claude); otherwise list these options in chat and wait for the user's answer.

> Which LLM provider should the smoke-test script call? Pick whichever you already have credentials for:
>
> **a. OpenAI** — needs `OPENAI_API_KEY` (starts with `sk-…`). LangChain package: `langchain-openai`.
> **b. Anthropic** — needs `ANTHROPIC_API_KEY` (starts with `sk-ant-…`). LangChain package: `langchain-anthropic`.
> **c. Google (Gemini)** — needs `GOOGLE_API_KEY`. LangChain package: `langchain-google-genai`.
> **d. AWS Bedrock** — uses your existing AWS credentials chain (`AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`, or `~/.aws/credentials`). LangChain package: `langchain-aws`.
> **e. Azure OpenAI** — needs `AZURE_OPENAI_API_KEY`, `AZURE_OPENAI_ENDPOINT`, and a deployment name. LangChain package: `langchain-openai` (the `AzureChatOpenAI` class).
> **f. Ollama (local models)** — no API key needed; needs `ollama serve` running on `localhost:11434` and a model pulled (e.g. `ollama pull llama3.2`). LangChain package: `langchain-ollama`.
> **g. Something else** — tell me which one and I'll look up the right `langchain-<provider>` package.

Wait for the user's choice. Record it as `llm_provider` (one of `openai`, `anthropic`, `google`, `bedrock`, `azure`, `ollama`, `custom`). All subsequent substeps depend on this value.

**Substep 3A.1b — Confirm credentials and apply the same storage choice as the Adrian key.** For every provider except Ollama (which has no API key), ask the user the credential question explicitly. Two parts:

1. **Do they have a credential ready?** If they don't, stop and help them get one — link to the provider's signup page. Do not invent a workaround.
2. **Use the same storage path they chose for the Adrian key in Step 1.2.** Branch on the `key_source` flag:

   - **`key_source = "env"`** → tell the user to add the provider key to the same `.env` file, e.g.:
     ```
     OPENAI_API_KEY=sk-…            # for option (a)
     ANTHROPIC_API_KEY=sk-ant-…     # for option (b)
     GOOGLE_API_KEY=…               # for option (c)
     AWS_REGION=us-east-1           # for option (d) — plus the access/secret keys, or rely on ~/.aws/credentials
     AZURE_OPENAI_API_KEY=…         # for option (e) — plus AZURE_OPENAI_ENDPOINT and AZURE_OPENAI_DEPLOYMENT
     # (Ollama needs no key)
     ```
     Ask them to confirm the file is saved before moving on. Verify the same way you verified the Adrian key (`cat .env` if you can't read files directly).

   - **`key_source = "inline_user"`** → do not ask the user to paste the provider key into chat. Instead tell them: "I'll write the script with a placeholder for your provider key too (e.g. `PASTE_YOUR_OPENAI_KEY_HERE`). You'll replace it in the script before running, the same way you'll replace the Adrian one." Set yourself a reminder to leave placeholders for both keys in Step 3A.3 and to prompt the user to fill them in before Step 3A.4.

   - **`key_source = "inline_agent"`** → ask them to paste the provider key into chat. You'll embed it inline in the script in Step 3A.3 with the same `# TODO: move to .env` marker used for the Adrian key. Confirm receipt (without echoing the key value) and proceed.

For **Ollama (option f)**, no key prompt is needed under any `key_source` — but do confirm: "Is `ollama serve` running and do you have at least one model pulled? If not, run `ollama pull llama3.2` (or any chat model) before we continue." Wait for confirmation.

For **AWS Bedrock (option d)**, also ask which model ID they want (e.g. `anthropic.claude-3-5-sonnet-20241022-v2:0`) — Bedrock has no default and the script needs to name one. For **Azure OpenAI (option e)**, ask for the deployment name.

Once credentials are confirmed and stored according to `key_source`, proceed to 3A.2.

**Substep 3A.2 — Create venv and install.** The LangChain provider package depends on `llm_provider`. Run, in the user's working directory:

```sh
python3 -m venv .venv
source .venv/bin/activate            # on Windows: .venv\Scripts\activate
pip install adrian-sdk <provider-package>
```

Substitute `<provider-package>` with:

| `llm_provider` | Package |
|---|---|
| `openai` | `langchain-openai` |
| `anthropic` | `langchain-anthropic` |
| `google` | `langchain-google-genai` |
| `bedrock` | `langchain-aws` |
| `azure` | `langchain-openai` (same package; uses `AzureChatOpenAI`) |
| `ollama` | `langchain-ollama` |
| `custom` | whatever the user names (`langchain-groq`, `langchain-mistralai`, `langchain-cohere`, etc.) |

If `pip install` exits non-zero, jump to Part 2 §12.

**Substep 3A.3 — Explain the script, then write it.** Before creating any file, tell the user in plain English what the script will do — they should never be in the position of pasting and running code without knowing what it does.

Send this explanation first (adapted to the chosen `llm_provider`):

> Here's what I'm about to create. The script `adrian_quickstart.py` is a tiny LangChain agent that does three things:
>
> 1. Calls `adrian.init()` to start Adrian's instrumentation. Adrian monkey-patches LangChain so every model call and tool call is captured automatically.
> 2. Builds a single chat-model client using **{provider name}** ({model id we picked}) and asks it one short question: *"In one sentence, why is the sky blue?"*. That's it — no tools, no agent loop, just one model call so we have something visible in the dashboard quickly.
> 3. Prints the model's reply and calls `adrian.shutdown()` to flush events.
>
> Side effects: it writes captured events to `./events.jsonl` next to the script, and pushes them over WebSocket to the Adrian backend. It does not write or modify anything else.
>
> Ready for me to create it? (yes/no)

Wait for the user to confirm before writing the file. Now create `adrian_quickstart.py` in the working directory. **The exact code depends on both `key_source` (from Step 1.2) and `llm_provider` (from Step 3A.1)** — assemble the script from the two pieces below.

##### Piece 1 — Adrian initialisation block

Pick exactly one of the three, matching `key_source`:

**A. `key_source = "env"`** (user saved the Adrian key to `.env`):
```python
    if not os.environ.get("ADRIAN_API_KEY"):
        sys.exit("ADRIAN_API_KEY missing. Did you source your .env? "
                 "Try: set -a; . ./.env; set +a")
    adrian.init()  # reads ADRIAN_API_KEY from env automatically
```

**B. `key_source = "inline_user"`** (user will edit the script before running). Leave the placeholder literally as-is — the user fills it in. **Keep the TODO comment** so the placeholder is easy to find:
```python
    # TODO: replace the PASTE_YOUR_KEY_HERE placeholder below with your actual
    # Adrian API key, then move it to a .env file before committing this script.
    # See https://docs.adrian.secureagentics.ai/quickstart for the .env pattern.
    adrian.init(api_key="adr_live_PASTE_YOUR_KEY_HERE")
```

After writing the file, remind the user: "Before you run this, open `adrian_quickstart.py` in your editor and replace `adr_live_PASTE_YOUR_KEY_HERE` with the real key you copied from the dashboard. Do the same for any other placeholder I left."

**C. `key_source = "inline_agent"`** (user pasted the Adrian key into chat). Substitute the literal key in place of `adr_live_REPLACE_ME`. **Keep the TODO comment** — it's the audit trail for the convenience trade-off the user opted into:
```python
    # TODO: move this key to a .env file before committing this script.
    # Replace the literal with os.environ["ADRIAN_API_KEY"] and put
    # ADRIAN_API_KEY=adr_live_... in .env (which should be gitignored).
    adrian.init(api_key="adr_live_REPLACE_ME")
```

##### Piece 2 — LLM provider block

Pick the block that matches `llm_provider`. Storage handling follows `key_source`:
- `env` → no key in code; the script asserts the relevant env var is set.
- `inline_user` → leave a `PASTE_YOUR_<PROVIDER>_KEY_HERE` placeholder with the same TODO comment pattern.
- `inline_agent` → inline the literal key the user pasted with the same TODO comment pattern.

**`openai`** — import `from langchain_openai import ChatOpenAI`; build with `ChatOpenAI(model="gpt-4o-mini")`. Key handling: `key_source = "env"` → add an upfront check for `OPENAI_API_KEY`; `key_source = "inline_user"` → at the top of `main()` set `os.environ["OPENAI_API_KEY"] = "PASTE_YOUR_OPENAI_KEY_HERE"` (with TODO comment); `key_source = "inline_agent"` → same line but substitute the actual `sk-…` value the user pasted.

**`anthropic`** — import `from langchain_anthropic import ChatAnthropic`; build with `ChatAnthropic(model="claude-3-5-haiku-latest")`. Env / inline pattern uses `ANTHROPIC_API_KEY`. Placeholder for `inline_user`: `PASTE_YOUR_ANTHROPIC_KEY_HERE`.

**`google`** — import `from langchain_google_genai import ChatGoogleGenerativeAI`; build with `ChatGoogleGenerativeAI(model="gemini-1.5-flash")`. Env / inline pattern uses `GOOGLE_API_KEY`. Placeholder for `inline_user`: `PASTE_YOUR_GOOGLE_KEY_HERE`.

**`bedrock`** — import `from langchain_aws import ChatBedrockConverse`; build with `ChatBedrockConverse(model="<model-id-the-user-named>", region_name=os.environ.get("AWS_REGION", "us-east-1"))`. For `env`, rely on the AWS credentials chain; for `inline_user`, leave placeholders for `AWS_ACCESS_KEY_ID` / `AWS_SECRET_ACCESS_KEY` / `AWS_REGION`; for `inline_agent`, inline the values the user pasted. All paths get the same TODO marker.

**`azure`** — import `from langchain_openai import AzureChatOpenAI`; build with `AzureChatOpenAI(azure_deployment="<deployment-name>", api_version="2024-10-21")`. Env / inline pattern uses `AZURE_OPENAI_API_KEY` + `AZURE_OPENAI_ENDPOINT`. Placeholder for `inline_user`: `PASTE_YOUR_AZURE_KEY_HERE`.

**`ollama`** — import `from langchain_ollama import ChatOllama`; build with `ChatOllama(model="<model-the-user-pulled>")`. No API key handling under any `key_source`; just confirm `ollama serve` is reachable at `http://localhost:11434` (the default).

**`custom`** — import what the user names and build with sensible defaults; ask the user for the class name, model id, and env-var name if you don't know them. Apply the same `key_source` rules to the env-var name they give you.

##### Full template

Assemble the chosen blocks into this skeleton (this example shows `key_source = "env"` + `llm_provider = "openai"`; substitute the blocks above for your case):

```python
"""Adrian 60-second smoke test.

This script makes one LLM call ("Why is the sky blue?") through LangChain
so that Adrian's auto-instrumentation captures a real model event. The
event will appear in the dashboard at https://app.adrian.secureagentics.ai/events
within a couple of seconds and is also written to ./events.jsonl locally.
"""
import asyncio
import os
import sys
import adrian
from langchain_openai import ChatOpenAI  # <-- swap to your provider's import


async def main() -> int:
    # === Adrian init (Piece 1) ===
    if not os.environ.get("ADRIAN_API_KEY"):
        sys.exit("ADRIAN_API_KEY missing. Did you source your .env? "
                 "Try: set -a; . ./.env; set +a")
    adrian.init()

    # === Provider key check (Piece 2) ===
    if not os.environ.get("OPENAI_API_KEY"):
        sys.exit("OPENAI_API_KEY missing. Set it in your shell or .env.")

    # === Model call ===
    llm = ChatOpenAI(model="gpt-4o-mini")
    response = await llm.ainvoke("In one sentence, why is the sky blue?")
    print("LLM said:", response.content)

    adrian.shutdown()
    return 0


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
```

After writing the file, show the user the final assembled code (or a diff if your client renders one) and ask: "Saved. Want me to run it now?" Wait for confirmation before executing in Step 3A.4.

**Substep 3A.4 — Run it.** Run from the same shell that has the venv active. The exact command depends on `key_source` *and* `llm_provider`. Tell the user which command to run — don't make them guess.

- **`key_source = "env"`** (recommended) — source `.env` once so every key in it (Adrian + whichever provider variables apply) is in the process environment, then run:
  ```sh
  set -a; . ./.env; set +a
  python adrian_quickstart.py
  ```
  On Windows PowerShell, use `Get-Content .env | ForEach-Object { $k,$v = $_ -split '=',2; [Environment]::SetEnvironmentVariable($k,$v) }` or have the user install `python-dotenv` and add `from dotenv import load_dotenv; load_dotenv()` near the top of the script.

- **`key_source = "inline_user"`** — before running, remind the user to open `adrian_quickstart.py` and replace every `PASTE_YOUR_..._HERE` placeholder with the actual values. Once they confirm the placeholders are filled in, run:
  ```sh
  python adrian_quickstart.py
  ```
  If you can read files, double-check there are no remaining `PASTE_YOUR_` substrings in the file before running. If you can't read files, ask the user to confirm explicitly: "Have you replaced every `PASTE_YOUR_…` placeholder?" Wait for a yes.

- **`key_source = "inline_agent"`** — both the Adrian key and the provider key are already inlined in the script (with TODO markers). Just run it:
  ```sh
  python adrian_quickstart.py
  ```
  Exception: if `llm_provider = "bedrock"` and the user relies on `~/.aws/credentials` rather than inlined access keys, the AWS SDK will pick those up automatically — nothing extra to export.
  Exception: if `llm_provider = "ollama"`, confirm `ollama serve` is reachable: `curl http://localhost:11434/api/tags` should return JSON.

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

**Substep 3B.3 — Identify the patch site.** The patch is two insertions, and the second one differs based on the `key_source` flag set in Step 1.2.

1. **Imports block at the top of the file:** add `import adrian`. Add `import os` too if `os` isn't already imported (only needed for the env variant below).

2. **Entry function — first statement inside the `async def`.** Find the function the user runs as the agent's entry point. This is usually the `async def main():` (or similar) that's called from `asyncio.run(main())`. Insert one of the three variants below as the first statement inside that function.

   #### Variant A — `key_source = "env"` (user has `.env` set up)

   ```python
       adrian.init(api_key=os.environ["ADRIAN_API_KEY"])
   ```

   The user is responsible for sourcing `.env` before running their agent. If they already use `python-dotenv` or `direnv`, this just works. If they don't, mention it: "Source your `.env` before running with `set -a; . ./.env; set +a`."

   #### Variant B — `key_source = "inline_user"` (user will edit the patched file themselves)

   ```python
       # TODO: replace the PASTE_YOUR_KEY_HERE placeholder below with your actual
       # Adrian API key, then move it to a .env file before committing this script.
       adrian.init(api_key="adr_live_PASTE_YOUR_KEY_HERE")
   ```

   **Keep the placeholder literally as-is** — the user fills it in. After applying the patch in 3B.4, remind the user explicitly: "Open the patched file in your editor and replace `adr_live_PASTE_YOUR_KEY_HERE` with the real key before running."

   #### Variant C — `key_source = "inline_agent"` (user pasted the key directly)

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

## Step 5 — Ask the user what they want to do next (default is stop)

**Do not auto-chain to anything.** The next-step prompt depends on which path the user came through — Step 3A (smoke test) or Step 3B (their own agent). The two cases are structurally similar but the phrasing matters: don't offer 3B users a "instrument an existing project" option when they just did exactly that.

Use a structured multi-choice prompt if your client supports one (`AskUserQuestion` on Claude); otherwise list in chat and wait for "a" or "b". The default is always (a) stop — never push the user toward the secondary option.

### 5.A — If the user came through Step 3A (test agent / smoke test)

> Adrian is capturing events from the test script — that's everything the setup flow needed to show you. What would you like to do next?
>
> **(a) Stop here. [default]** You've seen the loop work end-to-end. You can come back to this guide any time to instrument a real agent.
>
> **(b) Instrument an existing LangChain / LangGraph project of yours.** Tell me the file path and I'll add the same two lines (`import adrian` + `adrian.init(...)` in the async entry function) so Adrian captures events from your real code too. Same provider, same key, no other changes.

If the user picks **(b)**, jump to **Step 3B**, starting at 3B.1.

### 5.B — If the user came through Step 3B (already instrumented their own agent)

> Adrian is now instrumented in your agent at `<file path>` and capturing events. What would you like to do next?
>
> **(a) Stop here. [default]** Your agent will keep running with Adrian observing every LLM call and tool call — no further setup needed. Run the agent as you normally would.
>
> **(b) Instrument another agent file.** If you have additional LangChain / LangGraph entry points (multiple agents in one project, or a separate project), point me at the next file and I'll do the same patch.

If the user picks **(b)**, jump back to **Step 3B.1** with the new file path. Reuse the existing venv if the file is in the same directory; otherwise repeat the install per 3B.5. Do not silently change the LLM provider — keep using whatever the user's project already uses.

### Shared rules for both 5.A and 5.B

If the user picks **(a)** — stop cleanly. Summarise what was set up in one or two sentences (key created, smoke test ran or agent instrumented, first events visible) and say goodbye. Do not keep offering things. Do not volunteer Block mode, webhooks, agent remit, or anything else — they asked to stop. Stop.

**Only if the user proactively asks about further configuration**, point them at the relevant Part 2 sections — don't list these unprompted:

- Switching the agent profile to Block mode (halts risky tool calls mid-flight) → see §6 (Execution modes and the MAD taxonomy).
- Setting up a Discord webhook for M3 / M4 alerts → see §7 (Integrations → Notifications).
- Filling in the agent's remit / known risks so the classifier is more accurate → see §8 (Reading events → Settings → Agents) or edit the profile directly in the dashboard.
- Anything else → §12 (failure modes) or the Discord linked in §2.

The rest of this file is reference for when something goes wrong.

---

## Cross-client compatibility notes

| Agent | Multi-choice tool | File write | Shell execution | Special quirks |
|---|---|---|---|---|
| **Claude Code** (CLI) | none — ask in chat | Edit/Write tools | Bash tool | Sync via Edit; preserves diffs cleanly. Bash runs in the same shell cwd as the user expects. |
| **Claude Desktop** (Cowork) | `AskUserQuestion` | Edit/Write tools | sandboxed bash | Sandboxed bash has its **own** cwd, separate from the user's terminal — *never* use bare `touch .env`; always use the absolute `working_dir` path from Step 0. Chat persists, so prefer `.env` over `inline_agent`. |
| **Codex CLI** | none — ask in chat | direct edit | shell exec | Streaming edits — show diff first. Shell exec runs in Codex's session cwd, which may differ from the user's terminal cwd; confirm it matches `working_dir`. |
| **Codex web / ChatGPT** | none — ask in chat | offer code blocks | none | Can't execute or write files — the user runs every command. Hand them the absolute-path shell command from Step 1.3 verbatim. |
| **Cursor** chat | none — ask in chat | Composer can write | terminal panel | Composer multi-file edits work for the own-agent path. Restricted mode can't write `.env` — fall back to giving the user the shell command. Cursor's terminal panel may open at the project root, which may differ from `working_dir`; confirm. |
| **Aider** | none — ask in chat | yes (its model) | shell exec | Map files into the session before patching. Aider's cwd is wherever the user invoked it from — should match `working_dir` if the user followed Step 0. |
| **Windsurf / Copilot Chat** | none — ask in chat | varies | varies | Treat as Codex-equivalent. Same cwd-mismatch risk; always use absolute paths from `working_dir`. |

### Critical: the `.env`-vs-cwd mismatch (the single most common failure mode across clients)

The agent you're running may have a working directory that **does not match the user's terminal cwd**. Examples: Claude Desktop's sandboxed bash has a session-mount cwd; Codex CLI starts a session in whichever folder the user invoked it from, which may not be where they intend to run the script; Cursor's terminal panel may open at the project root rather than a subfolder. If you create `.env` via your own shell tool and the user runs the script from their terminal, the two cwds may point at different folders — the script won't see the key and you'll spend time debugging a phantom "key missing" error.

The fix is universal and already baked into Step 0 and Step 1.3, but worth restating:

1. **Always establish `working_dir` as an absolute path in Step 0** before any file-creation step. Announce it back to the user and get explicit confirmation.
2. **Every file-creation command must use the absolute path** — never `touch .env`, always `touch "<working_dir>/.env"`.
3. **Whenever you verify a file exists**, check it at `<working_dir>/<filename>` explicitly, not at whatever your shell's current cwd happens to be.
4. **If your shell tool's cwd differs from `working_dir`**, either `cd` to `working_dir` first or pass the absolute path to every command. Don't assume the user's terminal will be in the same place as yours.

If your client supports it, prefer:
- A structured multi-choice question over a free-text "type a or b". Reduces parse errors.
- Showing a diff before writing patched code (own-agent path) over a silent write. Users want a chance to bail.
- Reading `.env` once and not echoing the key back over re-reading it in every step. Reduces leak surface.
- Absolute paths in every file-touching command, even when you "know" the cwd is right. The cost of the prefix is two seconds; the cost of debugging an `.env` in the wrong folder is ten minutes.

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

When the user says "it doesn't work", before debugging anything else have them run a minimal smoke test using **whatever LLM provider they actually have credentials for** — not necessarily OpenAI. Pick the import and constructor for their provider; everything else stays the same.

```python
import asyncio, os, adrian
# Swap this import line for the user's actual provider:
#   from langchain_openai import ChatOpenAI                     # OpenAI
#   from langchain_anthropic import ChatAnthropic               # Anthropic
#   from langchain_google_genai import ChatGoogleGenerativeAI   # Google
#   from langchain_aws import ChatBedrockConverse               # AWS Bedrock
#   from langchain_openai import AzureChatOpenAI                # Azure OpenAI
#   from langchain_ollama import ChatOllama                     # Ollama (local)
from langchain_openai import ChatOpenAI  # example

async def smoke():
    assert os.environ.get("ADRIAN_API_KEY"), "ADRIAN_API_KEY missing"
    # Assert the env var for whichever provider was chosen (OPENAI_API_KEY,
    # ANTHROPIC_API_KEY, GOOGLE_API_KEY, AZURE_OPENAI_API_KEY, AWS_REGION+creds, ...).
    # Ollama needs no key — skip the assert.
    adrian.init(log_level="DEBUG")
    out = await ChatOpenAI(model="gpt-4o-mini").ainvoke("say ok")
    print("LLM:", out.content)
    adrian.shutdown()

asyncio.run(smoke())
```

**Do not substitute a fake / mock LLM** here either, even for diagnostic purposes — a fake LLM does not generate the LangChain callbacks Adrian listens for, so a passing fake-LLM smoke test tells you nothing about whether the real instrumentation works.

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

Behavioural rules (these override default LLM tendencies):
  - LLM-AGNOSTIC: ask the user which provider they want (OpenAI,
    Anthropic, Google, AWS Bedrock, Azure, Ollama, Groq, Mistral, …).
    Never assume OpenAI. Whatever has a langchain-<provider> package
    works.
  - NEVER suggest or use a fake / mock / stub LLM (no FakeListChatModel,
    FakeChatModel, etc.). It generates no real callbacks, so the
    instrumentation is invisible. If the user has no provider key at
    all, help them get one — do not invent a workaround.
  - CROSS-CONFIRM every branching decision (provider, key storage path,
    next steps). Do not silently pick defaults.
  - SAME STORAGE for every secret in the flow: if the Adrian key went
    into .env, the provider key goes into the same .env; if the Adrian
    key was inlined with a TODO marker, the provider key is inlined the
    same way.
  - EXPLAIN code in plain English before asking the user to run it.

Refuse to fabricate framework support that is not in §7 of the guide.
When in doubt, read backend/internal/alerts/alerts.json for the exact
MAD definitions or point the user at the Discord.
```

---

*Last verified against the in-tree code on 2026-05-20. If the SDK version (`adrian.__version__`) has moved past 1.0.0, re-check §5 and §12 against the new release notes before quoting line-numbered behaviour.*
