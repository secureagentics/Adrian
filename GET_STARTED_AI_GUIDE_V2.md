# Adrian - Quick Start AI Guide V2

> For humans: this is a short setup guide you can give to an AI assistant
> such as Claude, Codex, Cursor, Windsurf, or ChatGPT. It is intentionally
> narrow: managed Adrian app only, local `.env` secrets only, and the fewest
> prompts needed to get your first event into the dashboard.
>
> For AI assistants: follow this guide in order. Keep the language simple.
> Move quickly, but do not hide what you are doing.

---

## What This Guide Is Allowed To Do

This guide is designed to be easy to review. It only asks the assistant to:

- check Python is available
- help the user create an Adrian API key in the managed dashboard
- create or update a local `.env` file for secrets
- add `.env` to `.gitignore` if the project uses git and it is not already ignored
- create a tiny `adrian_quickstart.py` example file, or edit one existing agent file the user points to
- create a Python virtual environment and install `adrian-sdk` plus the selected LangChain provider package
- run the example or the user's agent

This guide does not ask the assistant to delete files, upload code, change git
history, edit backend services, or paste API keys into chat.

---

## Simple Rules For The Assistant

1. Use the managed Adrian app: `https://app.adrian.secureagentics.ai`.
2. Store secrets in `.env` only. Do not ask the user to paste API keys into chat.
3. Do not write API keys directly into Python files.
4. Use a real LLM provider. Do not use fake, mock, or stub LLMs.
5. Pause only when user input is actually needed.
6. Explain file changes in one short paragraph before making them.
7. If a key has already been pasted into chat or hardcoded in a file, recommend
   revoking it and creating a fresh key stored in `.env`.
8. When editing `.env`, stop immediately after opening the file. Wait for the
   user to confirm they saved it before installing packages, creating scripts,
   or running anything.

Planned pause points:

1. Ask the user to confirm they have copied their Adrian API key from the
   dashboard.
2. Ask the user to save the Adrian API key in `.env`.
3. Ask whether to use the Simple Example Agent or integrate an existing agent.
4. If using the Simple Example Agent, ask which LLM provider to use.
5. Ask the user to save the selected provider values in `.env`.

Do not add extra confirmation prompts unless something is unclear or risky.

---

## Step 0 - Check The Folder And Python

Run these in the current folder:

```sh
pwd
python3 --version
python3 -m pip --version
```

If Python is older than 3.12, stop and ask the user to install Python 3.12 or
newer. Adrian's SDK requires Python `>=3.12`.

Tell the user the absolute folder path. Example:

> I will set Adrian up in `/absolute/path/to/project`. The `.env`,
> virtual environment, and quickstart file will live here.

---

## Step 1 - Get The Adrian API Key

Ask the user to open the managed dashboard:

`https://app.adrian.secureagentics.ai`

If this is their first time signing in:

1. Sign up with Google, Microsoft, or GitHub.
2. Follow the first-time onboarding until Adrian shows an API key.
3. Copy the API key. It starts with `adr_live_`.
4. Skip detailed agent configuration for now. The quickstart only needs the API
   key. The SDK handles the live Adrian connection automatically.

If they already have an account:

1. Go to **Configurations**.
2. Open the agent/API key area.
3. Create or copy an agent API key.

Tell the user:

> Keep the key copied somewhere local for the next step. Do not paste it into
> this chat. We will put it into `.env` on your machine.

Pause here and ask:

> Do you have your Adrian API key ready?

---

## Step 2 - Create `.env`

Create and open a local `.env` file in the working folder. Use the absolute path
when creating or opening the file so the user knows exactly where it lives.

If the project has a `.gitignore`, make sure it contains:

```gitignore
.env
```

Use the command for the user's operating system. Replace
`/absolute/path/to/project` or `C:\absolute\path\to\project` with the working
folder from Step 0.

```sh
# macOS
touch "/absolute/path/to/project/.env" && open -a TextEdit "/absolute/path/to/project/.env"
```

```sh
# Linux
touch "/absolute/path/to/project/.env" && ${EDITOR:-nano} "/absolute/path/to/project/.env"
```

```powershell
# Windows PowerShell
New-Item -ItemType File -Force "C:\absolute\path\to\project\.env"; notepad.exe "C:\absolute\path\to\project\.env"
```

Ask the user to paste this into the file, replacing the placeholder with their
real Adrian API key:

```env
ADRIAN_API_KEY=adr_live_replace_this
```

Then say:

> Save `.env`, close the editor, and come back here when you are done. Do not
> paste the key into chat.

Stop here. Do not continue to agent selection until the user confirms the file
is saved.

After they confirm, verify without printing full secrets:

- `ADRIAN_API_KEY` exists and starts with `adr_live_`
- there are no quote marks around the values
- there are no placeholder values left

If the assistant can read files, it may check `.env` directly but must not echo
the key back to the chat.

---

## Step 3 - Choose The Agent To Run

Ask:

> Adrian needs an agent to monitor. Do you want to:
>
> **A. Simple Example Agent** - use a tiny example agent that asks an LLM:
> "In one sentence, why is the sky blue?" Fastest route to your first event.
>
> **B. Integrate Adrian with one of my existing agents** - point me at your
> LangChain or LangGraph agent and I will integrate Adrian with it.

If the user chooses A, continue to Step 4A.

If the user chooses B, continue to Step 4B.

---

## Step 4A - Simple Example Agent

Ask:

> In order to set up the example agent, you need to provide an LLM. Pick your
> preference:
>
> **a. OpenAI** - needs `OPENAI_API_KEY`; package `langchain-openai`
> **b. Anthropic** - needs `ANTHROPIC_API_KEY`; package `langchain-anthropic`
> **c. Google Gemini** - needs `GOOGLE_API_KEY`; package `langchain-google-genai`
> **d. Azure OpenAI** - needs `AZURE_OPENAI_API_KEY`,
> `AZURE_OPENAI_ENDPOINT`, and `AZURE_OPENAI_DEPLOYMENT`; package
> `langchain-openai`
> **e. Ollama** - no API key; usually runs locally at
> `http://localhost:11434`; package `langchain-ollama`

For the chosen provider, open `.env` again using the same command style from
Step 2. Ask the user to add only that provider's values below the existing
Adrian key.

Use the matching block:

```env
# OpenAI
LLM_PROVIDER=openai
OPENAI_API_KEY=sk_replace_this

# Anthropic
LLM_PROVIDER=anthropic
ANTHROPIC_API_KEY=sk-ant-replace-this

# Google Gemini
LLM_PROVIDER=google
GOOGLE_API_KEY=replace_this

# Azure OpenAI
LLM_PROVIDER=azure
AZURE_OPENAI_API_KEY=replace_this
AZURE_OPENAI_ENDPOINT=https://your-resource.openai.azure.com
AZURE_OPENAI_DEPLOYMENT=your-deployment-name

# Ollama
LLM_PROVIDER=ollama
OLLAMA_MODEL=llama3.2
```

Then say:

> Save `.env`, close the editor, and come back here when you are done. Do not
> paste the provider key into chat.

Stop here. Do not install packages or create `adrian_quickstart.py` until the
user confirms `.env` is saved.

After they confirm, verify without printing full secrets:

- `LLM_PROVIDER` is one of `openai`, `anthropic`, `google`, `azure`, or `ollama`
- the selected provider's required values exist
- there are no placeholder values left for the selected provider

For Ollama, check whether it is running:

```sh
curl http://localhost:11434/api/tags
```

If Ollama is not running, ask the user to run:

```sh
ollama serve
ollama pull llama3.2
```

### Install

Create a virtual environment and install the SDK plus the provider package:

```sh
python3 -m venv .venv
source .venv/bin/activate
pip install adrian-sdk langchain-openai
```

Replace `langchain-openai` with the package for the chosen provider.

### Create `adrian_quickstart.py`

Before writing the file, say:

> I am going to create a tiny example agent. It initializes Adrian, asks an LLM
> "In one sentence, why is the sky blue?", prints the answer, and sends the
> event to your Adrian dashboard.

Use this file:

```python
import asyncio
import os
import sys

import adrian


def require_env(name: str) -> str:
    value = os.environ.get(name)
    if not value:
        sys.exit(f"{name} is missing. Add it to .env and run again.")
    return value


def build_llm():
    provider = os.environ.get("LLM_PROVIDER", "openai").strip().lower()

    if provider == "openai":
        require_env("OPENAI_API_KEY")
        from langchain_openai import ChatOpenAI

        return ChatOpenAI(model=os.environ.get("OPENAI_MODEL", "gpt-4o-mini"))

    if provider == "anthropic":
        require_env("ANTHROPIC_API_KEY")
        from langchain_anthropic import ChatAnthropic

        return ChatAnthropic(
            model=os.environ.get("ANTHROPIC_MODEL", "claude-3-5-haiku-latest")
        )

    if provider == "google":
        require_env("GOOGLE_API_KEY")
        from langchain_google_genai import ChatGoogleGenerativeAI

        return ChatGoogleGenerativeAI(
            model=os.environ.get("GOOGLE_MODEL", "gemini-1.5-flash")
        )

    if provider == "azure":
        require_env("AZURE_OPENAI_API_KEY")
        require_env("AZURE_OPENAI_ENDPOINT")
        deployment = require_env("AZURE_OPENAI_DEPLOYMENT")
        from langchain_openai import AzureChatOpenAI

        return AzureChatOpenAI(
            azure_deployment=deployment,
            api_version=os.environ.get("AZURE_OPENAI_API_VERSION", "2024-10-21"),
        )

    if provider == "ollama":
        from langchain_ollama import ChatOllama

        return ChatOllama(
            model=os.environ.get("OLLAMA_MODEL", "llama3.2"),
            base_url=os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434"),
        )

    sys.exit(
        "Unsupported LLM_PROVIDER. Use openai, anthropic, google, azure, or ollama."
    )


async def main() -> int:
    require_env("ADRIAN_API_KEY")

    adrian.init()
    llm = build_llm()
    response = await llm.ainvoke("In one sentence, why is the sky blue?")
    print(response.content)
    adrian.shutdown()
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
```

### Run

Load `.env` and run the example:

```sh
set -a
. ./.env
set +a
python adrian_quickstart.py
```

If it prints an answer, say:

> Everything worked! Open `https://app.adrian.secureagentics.ai/events` and you
> should see the event within a few seconds. Then use the final success message
> below.

If the event does not appear, go to "If Anything Goes Wrong" below.

---

## Step 4B - Integrate Adrian With An Existing Agent

Ask:

> Please give me the absolute path to your LangChain or LangGraph agent file.
> If your assistant cannot read outside this folder, copy the agent file into
> this project and point me at that copy.

Read the file and check:

- it is Python
- it imports or uses LangChain or LangGraph
- it has an entry point such as `main()`, `async def main()`, or code that calls
  `.invoke()`, `.ainvoke()`, or `.astream()`

Before editing, say:

> I will integrate Adrian with this agent by importing `adrian`, initializing it
> near the start of the agent run, and keeping secrets in `.env`.

Patch the file:

1. Add `import adrian` near the imports.
2. Add this before the model, chain, or graph is created or called:

```python
adrian.init()
```

If the agent has a clear shutdown path, add:

```python
adrian.shutdown()
```

If it is a long-running app, mention that `adrian.shutdown()` should be called
from the app's normal shutdown hook.

Install the SDK in the existing environment:

```sh
pip install adrian-sdk
```

Run the agent the way the user normally runs it, after loading `.env`:

```sh
set -a
. ./.env
set +a
# then run the user's normal agent command
```

If the agent runs, say:

> Everything worked! Open `https://app.adrian.secureagentics.ai/events` and you
> should see events within a few seconds. Then use the final success message
> below.

---

## If Anything Goes Wrong

Keep troubleshooting short. Check these first:

### Python is too old

Adrian requires Python `>=3.12`.

### A secret is missing

Make sure `.env` contains:

```env
ADRIAN_API_KEY=...
```

For the Simple Example Agent, it also needs the selected provider key, unless
the provider is Ollama.

### The dashboard has no event

Check:

- the key starts with `adr_live_`
- the script was run after loading `.env`
- `events.jsonl` exists locally, which means Adrian captured the event

### The LLM provider fails

Check the provider key and package:

- OpenAI: `OPENAI_API_KEY`, `langchain-openai`
- Anthropic: `ANTHROPIC_API_KEY`, `langchain-anthropic`
- Google Gemini: `GOOGLE_API_KEY`, `langchain-google-genai`
- Azure OpenAI: Azure key, endpoint, deployment, `langchain-openai`
- Ollama: `ollama serve`, local model pulled, `langchain-ollama`

### A key was pasted into chat or hardcoded

Recommend this cleanup:

1. Revoke that key in the Adrian dashboard or provider dashboard.
2. Create a fresh key.
3. Store the fresh key only in `.env`.
4. Remove hardcoded keys from Python files.

---

## Final Success Message

When setup works, keep the final message simple:

> Everything worked! You have just integrated a security monitoring system
> around an agent. Every action that agent takes can now appear in your Adrian
> dashboard, where Adrian assesses whether the action looks safe or dangerous.
> You can use **Configurations** to give Adrian more context about the agent it
> is monitoring, choose whether to block dangerous actions or only receive
> alerts, and set up alerting through Discord or Slack.
