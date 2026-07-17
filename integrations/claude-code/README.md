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


## Requirements
- **Claude Code**
- **Python 3.12+** on your PATH (`python3`, `python`, or the `py` launcher on Windows)
- **Windows only:** Git for Windows (Git Bash). Claude Code runs plugin hooks
  under Git Bash by default; a `.cmd` launcher is bundled as a cmd.exe fallback.
- An Adrian backend and API key (Adrian Cloud, or a self-hosted OSS backend).
  `/adrian-init` walks you through this.

## Install

From inside Claude Code:

```
/plugin marketplace add secureagentics/Adrian
/plugin install adrian-cc@adrian
/adrian-init
```

1. **Add the marketplace** - registers the `adrian` marketplace from the
   Adrian repo.
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


## Commands

| Command | Description |
|---------|-------------|
| `/adrian-init` | Guided setup: choose backend, write `~/.adrian/.env`, verify the connection |
| `/adrian-status` | Show the configured backend URL, key presence, and connection health |


## Enforcement Modes

Enforcement is server-driven: the backend's policy decides the mode on login, and
the plugin acts accordingly. No behavior is hard-wired in the client.

| Mode | Behavior |
|------|----------|
| **ALERT** | Log only. Never blocks. |
| **BLOCK** | Deny in-scope high-risk tool calls before they execute. |
| **HITL** | Prompt you in Claude Code to approve or deny the tool call inline. |


## Features

- **Real-time blocking** - high-risk actions are denied before execution
- **Inline HITL** - approve or deny flagged tool calls right in Claude Code
- **Sub-agent tracking** - tracks Agent tool spawns with parent/child hierarchy
- **Tool output capture** - PostToolUse logs complete tool responses
- **Invocation tracking** - groups events by user prompt for context
- **Transcript parsing** - extracts Claude's reasoning and user instructions
- **Dashboard** - view events and verdicts at your Adrian dashboard
- **Fail-open** - if the backend is unavailable, tools are allowed by default

## Configuration

`/adrian-init` writes `~/.adrian/.env`.  Values
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
claude --plugin-dir /path/to/Adrian/integrations/claude-code
```

Then run `/adrian-init`, or export `ADRIAN_API_KEY` and `ADRIAN_WS_URL` yourself.


## License

Apache-2.0 - See [Adrian](https://github.com/secureagentics/Adrian)
