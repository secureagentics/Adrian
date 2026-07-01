# Adrian architecture

```text
+----------------------+        WS / Protobuf
|  User's agent +      | <----------------------------+
|  Adrian SDK          | ---------------------------+ |
+----------------------+                            | |
                                                    v |
+--------------------------------------------------------------------+
|  adrian-backend  (single Go container)                             |
|                                                                    |
|  internal/ws          SDK WebSocket: API-key auth on upgrade,      |
|                       paired_batch ingest, 10s ping / 15s pong     |
|                       heartbeat, verdict push back to the SDK.     |
|                              |                                     |
|                              v                                     |
|  internal/engine      Sliding window keyed by                      |
|                       (session_id, invocation_id, agent_id);       |
|                       16-slot ring buffer per key with a per-key   |
|                       mutex (process-local). Prompt build          |
|                       (system + few-shot + history + trace),       |
|                       HTTP POST to ADRIAN_LLM_URL (OpenAI          |
|                       compatible chat-completions), strip          |
|                       <reasoning> blocks, parse M-code. On         |
|                       classifier error, return an error to WS      |
|                       ingest. WS persists verdict_status=error,    |
|                       mad_code="", and routes by policy.           |
|                              |                                     |
|                              v                                     |
|  internal/store       SQLite (WAL) writes: events, verdicts,       |
|                       hitl_queue, audit_log, agents, mcp_servers.  |
|                              |                                     |
|                              +--> verdict pushed back to SDK       |
|                              +--> internal/notifications dispatch  |
|                                   Discord webhook on M3 / M4       |
|                                   (human-readable text sourced     |
|                                    from internal/alerts).          |
|                                                                    |
|  internal/api         Dashboard REST: session-cookie auth.         |
|                       login, agents, API keys, policy, events,     |
|                       verdicts, reviews, webhooks, MCP, audit-log. |
|                                                                    |
|  stdout: structured slog (JSON) logs.                              |
+--------------------------------------------------------------------+
                                  ^
                                  | REST /api/*
                                  |
+--------------------------------------------------------------------+
|  adrian-frontend  (Next.js container)                              |
|   Login and force-change-password, agent profiles, API keys,       |
|   policy editor (singleton mode, per-MAD-code toggles, classifier  |
|   error fail-closed flag), HITL review queue, events and verdicts  |
|   feeds (REST poll), webhook configuration (Discord).              |
+--------------------------------------------------------------------+

+--------------------------------------------------------------------+
|  adrian-llm  (compose --profile llm; NVIDIA GPU required)          |
|   Llama.cpp server, --model /models/<file>.gguf.                   |
|   OpenAI-compatible /v1/chat/completions on :8081.                 |
|   Target of ADRIAN_LLM_URL (set by adrian-setup bootstrap).        |
+--------------------------------------------------------------------+

+--------------------------------------------------------------------+
|  adrian-setup  (compose --profile setup; one-shot)                 |
|   Subcommands: bootstrap, reset-password, set-model,               |
|   apply-migrations. Runs against the ./data and ./models volumes.  |
+--------------------------------------------------------------------+

Host volumes
  ./models -> /models  (adrian-llm)       GGUF model file
  ./data   -> /data    (adrian-backend)   SQLite database
```
