# @secureagentics/adrian

Core TypeScript SDK for Adrian multi-agent security monitoring in Node.js.

Handles the event pipeline: callback handler, event pairing, PII redaction, JSONL logging, WebSocket streaming, MCP inventory, and BLOCK/HITL tool gating.

## Install

You usually do **not** need to install this package directly. Provider packages install it automatically:

```bash
npm install @secureagentics/adrian-openai openai   # includes @secureagentics/adrian
```

Install core on its own only if you are wiring callbacks manually or building a custom integration:

```bash
npm install @secureagentics/adrian
```

## Quick start (via a provider package)

```ts
import OpenAI from "openai";
import { init, shutdown, adrian } from "@secureagentics/adrian-openai";

await init({ apiKey: process.env.ADRIAN_API_KEY });
const openai = adrian(new OpenAI());
await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Hello" }],
});
await shutdown();
```

## Core exports

| Export | Description |
|---|---|
| `init(options?)` | Initialise the SDK |
| `shutdown()` | Flush handlers and tear down |
| `getHandler()` | Access the callback handler for manual wiring |
| `getWebSocketClient()` | Access the WebSocket client |
| `AdrianCallbackHandler` | Event callback handler class |
| `JSONLHandler` | Local JSONL event sink |

## Environment

- `ADRIAN_API_KEY` — API key used for WebSocket authentication.
- `ADRIAN_LOG_FILE` — local JSONL log path. Defaults to `events.jsonl`.
- `ADRIAN_WS_URL` — Adrian WebSocket endpoint. Defaults to `ws://localhost:8080/ws`.
- `ADRIAN_SESSION_ID` — session identifier for grouping events.
- `ADRIAN_BLOCK_TIMEOUT` — seconds to wait for BLOCK/HITL verdicts.
- `ADRIAN_REPLAY_BUFFER_FRAMES` — number of WebSocket frames buffered while disconnected.

Set `wsUrl: null` in `init()` for local JSONL logging without a WebSocket connection:

```ts
import { init, shutdown } from "@secureagentics/adrian";

await init({
  wsUrl: null,
  logFile: "events.jsonl",
  onEvent: (eventType, data, runId, parentRunId, eventId) => {
    console.log({ eventType, runId, parentRunId, eventId, data });
  },
});

await shutdown();
```

## Manual callback wiring

```ts
import { init, getHandler } from "@secureagentics/adrian";

await init();
const handler = getHandler();
// Pass handler into your framework's callback system.
```

For custom integrations, pair an LLM start and end with the same `runId`:

```ts
import { randomUUID } from "node:crypto";
import { init, shutdown, getHandler } from "@secureagentics/adrian";

await init({ wsUrl: null });

const handler = getHandler();
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

await shutdown();
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

## Custom event handlers

Provide `handlers` when you want to replace the default JSONL/WebSocket sinks:

```ts
import { init, type EventHandler, type PairedEvent } from "@secureagentics/adrian";

const handler: EventHandler = {
  onPairedEvent(event: PairedEvent) {
    console.log(event.pairType, event.eventId);
  },
  close() {
    // Flush resources if needed.
  },
};

await init({ handlers: [handler] });
```

## Subpath export

`@secureagentics/adrian/capture` exposes shared LLM capture helpers used internally by provider packages. Most apps should use `adrian()` from a provider package instead.
