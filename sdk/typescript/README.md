# Adrian TypeScript SDK

Monorepo for the Adrian TypeScript SDK. The core package owns the event pipeline: event pairing, PII redaction, JSONL logging, WebSocket streaming, policy verdicts, and shared capture helpers.

## Requirements

Node.js 18 or later.

## Packages

| Package | npm name | Install | Import |
|---|---|---|---|
| Core | `@secureagentics/adrian` | `npm install @secureagentics/adrian` | `import { adrian } from "@secureagentics/adrian"` |
| [OpenAI](packages/openai/README.md) | `@secureagentics/adrian-openai` | `npm install @secureagentics/adrian-openai openai` | `import { adrian } from "@secureagentics/adrian-openai"` |

## Environment

Explicit `init()` options take precedence over environment variables.

| Variable | Description |
|---|---|
| `ADRIAN_API_KEY` | API key used for WebSocket authentication |
| `ADRIAN_LOG_FILE` | Local JSONL log path (default: `events.jsonl`) |
| `ADRIAN_WS_URL` | WebSocket endpoint (default: `ws://localhost:8080/ws`) |
| `ADRIAN_SESSION_ID` | Session identifier for grouping events |
| `ADRIAN_BLOCK_TIMEOUT` | Seconds to wait for a BLOCK-mode verdict before fail-open (default: `30`) |
| `ADRIAN_REPLAY_BUFFER_FRAMES` | WebSocket replay buffer size (default: `1000`) |

## Policy and BLOCK mode

When connected over WebSocket and the dashboard policy is in **BLOCK** or **HITL** mode, the SDK waits for backend verdicts on tool calls proposed by an LLM turn. In **BLOCK** mode, if no verdict arrives within `blockTimeout` seconds, the SDK **fail-open** and allows execution (matching the Python SDK). Dashboard-configurable failure policy is planned for a later release.

## Manual callback wiring

For custom integrations, pair an LLM start and end with the same `runId`:

```ts
import { randomUUID } from "node:crypto";
import { adrian } from "@secureagentics/adrian";

await adrian.init({ wsUrl: null });

const handler = adrian.getHandler();
const runId = randomUUID();

await handler?.handleChatModelStart(
  { name: "custom-model" },
  [[{ role: "user", content: "Hello" }]],
  runId,
);

await handler?.handleLLMEnd(
  {
    output: "Hi there",
    toolCalls: [],
    usage: { promptTokens: 1, completionTokens: 2, totalTokens: 3 },
  },
  runId,
);

await adrian.shutdown();
```

Manual tool events work the same way:

```ts
const toolRunId = randomUUID();

await handler?.handleToolStart(
  { name: "lookup_user" },
  JSON.stringify({ userId: "user_123" }),
  toolRunId,
  undefined,
  { tool_call_id: "call_123", metadata: { source: "custom-integration" } },
);

await handler?.handleToolEnd(JSON.stringify({ ok: true }), toolRunId);
```

## Subpath export

`@secureagentics/adrian/capture` exposes shared LLM capture helpers used internally by provider packages.

## Development

From this directory:

```sh
npm install
npm run build
npm test
```

Build or test the core package only:

```sh
npm run build -w @secureagentics/adrian
npm test -w @secureagentics/adrian
```
