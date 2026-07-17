---
description: Set up Adrian security - choose the backend and configure your API key.
---

Walk the user through Adrian setup in a SINGLE run - concisely, step by step.

CRITICAL - do the WHOLE flow in ONE invocation. NEVER finish by telling the user
to run `/adrian-init` again. If a check fails, that is your cue to CONTINUE with
the setup steps below yourself. In particular: if you run `verify` first and it
FAILS, do NOT stop and re-prompt - go straight to step 1 and continue the setup
in the same run.

SECURITY - non-negotiable: NEVER ask the user to paste their API key into the
chat, and NEVER print, `cat`, or `echo` the key value or the contents of the
`.env` file. The key stays out of this conversation. You only ever run the
`verify` command (which reports OK/FAIL, not the key).

## 0. (Optional) Short-circuit if already configured
You MAY first run `adrian-python -m adrian_cc.agent verify`:
- **OK** → Adrian is already set up; say so and stop. Nothing else to do.
- **FAIL** (or not configured) → this is expected for first-time setup. Do NOT
  stop, and do NOT ask the user to re-run the command - immediately continue to
  step 1 in this same run.

## 1. Choose the backend
Use the **AskUserQuestion** tool (header "Backend"). The tool auto-adds an "Other"
free-text choice = the custom path; word it so the user knows Other = a custom
self-hosted URL:
- "Adrian Cloud (hosted)" - managed backend. **(Recommended)**
- "Self-hosted (local)" - a local OSS backend on the default host/port.
- (built-in "Other") - the user types a custom self-hosted host/port or ws:// URL.

Resolve the answer to a WebSocket URL:
- Adrian Cloud (hosted) → `wss://adrian.secureagentics.ai/ws`
- Self-hosted (local)   → `ws://localhost:8080/ws`
- "Other" free text     → normalize whatever they typed:
    - a full `ws://` or `wss://` URL → use as-is
    - `host:port` → `ws://host:port/ws`
    - `host` only → `ws://host:8080/ws`
  (Defaults: port 8080, path `/ws`, scheme `ws://` unless they gave `wss://`. If
  their input is ambiguous, confirm the resolved URL before writing.)

## 2. Write the config (commented, with an API-key PLACEHOLDER)
Create `~/.adrian/` if needed and write `~/.adrian/.env` with `ADRIAN_WS_URL` set
to the resolved URL and a short `#` comment above each variable explaining what
it's for, so the user can understand and hand-tune the settings they weren't
asked about:
```
# Adrian backend WebSocket URL (set from your backend choice above).
ADRIAN_WS_URL=<resolved>

# Your Adrian API key - replace this placeholder with your real key:
# adr_live_... (Adrian Cloud, from your dashboard) or adr_local_... (self-hosted).
ADRIAN_API_KEY=REPLACE_WITH_YOUR_ADRIAN_KEY

# Seconds to wait for a verdict before giving up on a tool call.
ADRIAN_CC_VERDICT_TIMEOUT=15

# What happens if Adrian can't reach the backend or a verdict times out:
#   true  = fail open  - allow the tool (best-effort monitoring, never blocks work)
#   false = fail closed - block the tool (max security, but blocks work when the backend is down)
ADRIAN_CC_FAIL_OPEN=true
```
The `ADRIAN_API_KEY` line is a PLACEHOLDER written for the user, so all they have
to do is swap the value. **Preserve an existing real key:** if `~/.adrian/.env`
already has an `ADRIAN_API_KEY` line, do NOT overwrite it with the placeholder -
detect this silently with `grep -q '^ADRIAN_API_KEY=' ~/.adrian/.env` (exit
status only; it never prints the value). Never print the file contents or the
key. Tell the user the exact file path you wrote.

## 3. Ask the user to set their API key (out of band)
Tell the user to open `~/.adrian/.env` and REPLACE the `REPLACE_WITH_YOUR_ADRIAN_KEY`
placeholder with their real key - `adr_live_...` for Adrian Cloud (copied from
their dashboard) or `adr_local_...` for a self-hosted key. Do NOT accept the key
in the chat.

## 4. Confirm, then verify (the login is the check)
Do NOT grep or inspect `.env` for the key value - trust the user and let the
`verify` login be the single source of truth. Use **AskUserQuestion** with a
question that names the variable - question: "Replace the ADRIAN_API_KEY
placeholder in `~/.adrian/.env` with your key, then choose Verify", header:
"Add API key" - options ["Verify", "Cancel setup"].

On "Verify", run (the plugin's `bin/` is on your PATH while the plugin is
enabled):
```
adrian-python -m adrian_cc.agent verify
```
It reports only OK/FAIL + the backend mode - never the key:
- **OK** → tell the user Adrian is configured and monitoring is active for new
  sessions.
- **FAIL** → show the reason (a still-unreplaced placeholder or bad key surfaces
  as an auth/handshake error; a wrong URL as a connection error), tell them how
  to fix it, and AskUserQuestion ["Verify", "Cancel setup"] again - loop until OK
  or cancel. Do NOT tell them to re-run `/adrian-init`.

Finish by confirming the `~/.adrian/.env` path. Keep it friendly and brief.
