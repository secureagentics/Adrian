# Adrian CC - Claude Code Security Plugin

Runtime security plugin for Claude Code. It monitors every tool call, classifies
it with the Adrian backend (LLM-based MAD code classification), and blocks
malicious actions in real time.

## Architecture

```
Claude Code ──► Plugin Hook ──► Adrian Backend (WebSocket, protobuf)
                   │                    │
                   │                    ├─ Classify (M0/M2/M3/M4)
                   │                    ├─ Store event + verdict
                   │                    └─ Return verdict
                   │
                   ├─ allow  (benign / policy off)
                   ├─ ask    (HITL: prompt you to approve)
                   └─ deny   (block before the tool runs)
```

No daemon, no intermediary. Each hook call opens a direct WebSocket to the Adrian
backend, sends the event as protobuf, waits for a verdict, and returns
allow / deny / ask to Claude Code.

## Requirements

- **Claude Code**
- **Python 3.12+** on your PATH (`python3`, `python`, or the `py` launcher on Windows)
- **Windows only:** Git for Windows (Git Bash). Claude Code runs plugin hooks
  under Git Bash by default; a `.cmd` launcher is bundled as a cmd.exe fallback.
- An Adrian backend and API key (Adrian Cloud, or a self-hosted OSS backend).
  `/adrian-init` walks you through this.

You do **not** need to `pip install` anything: protobuf, websockets, and certifi
are vendored with the plugin.

## Install

From inside Claude Code:

```
/plugin marketplace add secureagentics/Adrian_OSS
/plugin install adrian-cc@adrian
/adrian-init
```

1. **Add the marketplace** - registers the `adrian` marketplace from the
   Adrian_OSS repo.
2. **Install the plugin** - `adrian-cc` from the `adrian` marketplace.
3. **Configure** - `/adrian-init` guides you through picking a backend (Adrian
   Cloud, self-hosted, or a custom URL), writes `~/.adrian/.env`, asks you to drop
   your API key into that file (never pasted into the chat), and verifies the
   connection.

Monitoring is active for new Claude Code sessions once `/adrian-init` reports OK.

### Updating

Bump happens through the marketplace. To pull the latest:

```
/plugin marketplace update adrian
```

### Team / enterprise preseed (optional)

Enable the plugin for everyone via `~/.claude/settings.json` (user) or
`.claude/settings.json` (project), so no one runs the `/plugin` commands by hand:

```json
{
  "extraKnownMarketplaces": {
    "adrian": {
      "source": { "source": "github", "repo": "secureagentics/Adrian_OSS" }
    }
  },
  "enabledPlugins": { "adrian-cc@adrian": true }
}
```

Each user still runs `/adrian-init` once to set their own API key.

## Commands

| Command | Description |
|---------|-------------|
| `/adrian-init` | Guided setup: choose backend, write `~/.adrian/.env`, verify the connection |
| `/adrian-status` | Show the configured backend URL, key presence, and connection health |

## What Gets Monitored

The plugin wires seven Claude Code hooks:

| Hook | What it does |
|------|--------------|
| **SessionStart** | Resets per-session state, injects a security-governance note |
| **UserPromptSubmit** | Logs your prompt for audit and classifier context (never blocks) |
| **PreToolUse** | Classifies the tool call, then allow / deny / ask per the active mode |
| **PostToolUse** | Logs the tool's output for the audit trail |
| **Stop** | Logs the assistant's final message for the turn |
| **Notification** | Logs Claude Code notifications |
| **SessionEnd** | Cleans up per-session state |

## Enforcement Modes

Enforcement is server-driven: the backend's policy decides the mode on login, and
the plugin acts accordingly. No behavior is hard-wired in the client.

| Mode | Behavior |
|------|----------|
| **ALERT** | Log only. Never blocks. |
| **BLOCK** | Deny in-scope high-risk tool calls before they execute. |
| **HITL** | Prompt you in Claude Code to approve or deny the tool call inline. |

## Classification Codes

| Code | Severity | Typical action | Examples |
|------|----------|----------------|----------|
| M0 | Benign | Allow | `ls`, `npm install`, `git status` |
| M2 | Medium | Advisory / act per org policy | Unvalidated inputs, weak crypto |
| M3 | High | Block (or prompt in HITL) | Reverse shells, command injection, data exfil |
| M4 | Critical | Block (or prompt in HITL) | Backdoors, credential theft, malware |

A code is only enforced when the org policy flag for it is enabled, so you can
tune exactly which codes act.

## Features

- **Real-time blocking** - high-risk actions are denied before execution
- **Inline HITL** - approve or deny flagged tool calls right in Claude Code
- **Sub-agent tracking** - tracks Agent tool spawns with parent/child hierarchy
- **Tool output capture** - PostToolUse logs complete tool responses
- **Invocation tracking** - groups events by user prompt for context
- **Transcript parsing** - extracts Claude's reasoning and user instructions
- **Dashboard** - view events and verdicts at your Adrian dashboard
- **Fail-open** - if the backend is unavailable, tools are allowed by default
- **Self-contained** - no pip install, no daemon, no background process

## Configuration

`/adrian-init` writes `~/.adrian/.env`. You can hand-edit it any time. Values
already set in the environment win; otherwise a project-local `.env` (in the
directory you launch Claude Code from) takes precedence over `~/.adrian/.env`.

| Variable | Default | Description |
|----------|---------|-------------|
| `ADRIAN_API_KEY` | *(required)* | API key for backend auth |
| `ADRIAN_WS_URL` | `ws://localhost:8080/ws` | Backend WebSocket URL (`wss://` for Adrian Cloud) |
| `ADRIAN_CC_VERDICT_TIMEOUT` | `15` | Seconds to wait for a verdict |
| `ADRIAN_CC_FAIL_OPEN` | `true` | Allow (`true`) or block (`false`) on timeout/error |

## Local / development install

To run from a checkout without the marketplace (deps are still vendored, so no
pip step):

```bash
claude --plugin-dir /path/to/Adrian_OSS/integrations/claude-code
```

Then run `/adrian-init`, or export `ADRIAN_API_KEY` and `ADRIAN_WS_URL` yourself.

## Plugin Structure

```
├── .claude-plugin/plugin.json   ← Plugin metadata (name, version, keywords)
├── hooks/hooks.json             ← Hook wiring (7 events)
├── commands/
│   ├── adrian-init.md           ← /adrian-init   guided setup
│   └── adrian-status.md         ← /adrian-status  config + health
├── bin/
│   ├── adrian-python            ← POSIX / Git Bash launcher (resolves vendored deps)
│   └── adrian-python.cmd        ← Windows cmd.exe fallback launcher
├── adrian_cc/                   ← Python package
│   ├── agent.py                 ← Hook handler (direct WS to backend)
│   ├── tracker.py               ← Agent hierarchy state
│   ├── transcript.py            ← Transcript parser
│   └── proto/                   ← Adrian protobuf (shared wire format with the SDK)
├── vendor/                      ← Bundled pure-Python deps (protobuf, websockets, certifi)
├── pyrightconfig.json           ← basedpyright (strict) config
└── pyproject.toml               ← Package definition + ruff config
```

## License

Apache-2.0 - See [Adrian_OSS](https://github.com/secureagentics/Adrian_OSS)
