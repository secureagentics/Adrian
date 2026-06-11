# Adrian TypeScript SDK

Monorepo for the Adrian TypeScript SDK. Pick the package for your framework - the core SDK is installed automatically.

The core package owns the event pipeline: event pairing, PII redaction, JSONL logging, WebSocket streaming, policy verdicts, and shared capture helpers. Provider packages, such as OpenAI, adapt framework-specific request and response shapes into that core pipeline.

## Packages

| Package | npm name | Install | Import |
|---|---|---|---|
| OpenAI | `@secureagentics/adrian-openai` | `npm install @secureagentics/adrian-openai openai` | `import { init, adrian, captureTool } from "@secureagentics/adrian-openai"` |
| Core only | `@secureagentics/adrian` | `npm install @secureagentics/adrian` | `import { init, shutdown } from "@secureagentics/adrian"` |

Provider packages depend on `@secureagentics/adrian` and re-export `init`, `shutdown`, and other core APIs — one install, one import.

## Two-step setup

1. **`init()`** — starts the event pipeline (JSONL, WebSocket, PII redaction).
2. **`adrian()`** — connects your framework to Adrian.

Both come from the same provider package:

```ts
import { init, adrian, captureTool } from "@secureagentics/adrian-openai";

await init({ apiKey: process.env.ADRIAN_API_KEY });
```

## Unified API

The OpenAI provider package exports the familiar Adrian entrypoints:

| Export | Purpose |
|---|---|
| `init` / `shutdown` | Re-exported from core |
| `adrian(...)` | Wrap an OpenAI client |
| `captureTool(...)` | Capture manual tool execution |

Shared option types (same names in every provider package):

| Type | Purpose |
|---|---|
| `AdrianOptions` | Optional metadata when wrapping a client or module |
| `ToolCallLike` | Shape of a tool call passed to `captureTool` |
| `ToolCaptureOptions` | Optional metadata when capturing tool execution |

## Examples

### OpenAI

```bash
npm install @secureagentics/adrian-openai openai
```

```ts
import OpenAI from "openai";
import { init, shutdown, adrian } from "@secureagentics/adrian-openai";

await init({ apiKey: process.env.ADRIAN_API_KEY });

const openai = adrian(new OpenAI());

const response = await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Hello" }],
});

await shutdown();
```

### OpenAI tool execution

OpenAI returns tool call requests; your app still executes the tools. Wrap that execution with `captureTool` so Adrian can apply BLOCK/HITL policy and capture the tool result:

```ts
import OpenAI from "openai";
import { init, shutdown, adrian, captureTool, AdrianPolicyBlockedError, BLOCKED_TOOL_MESSAGE } from "@secureagentics/adrian-openai";

await init({ apiKey: process.env.ADRIAN_API_KEY });

const openai = adrian(new OpenAI());
const messages: OpenAI.Chat.ChatCompletionMessageParam[] = [
  { role: "user", content: "What is the weather in Paris?" },
];

async function getWeather(city: string) {
  return { city, forecast: "sunny" };
}

const response = await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages,
  tools: [
    {
      type: "function",
      function: {
        name: "get_weather",
        description: "Get the current weather for a city",
        parameters: {
          type: "object",
          properties: { city: { type: "string" } },
          required: ["city"],
        },
      },
    },
  ],
});

const assistantMessage = response.choices[0]?.message;
if (!assistantMessage) throw new Error("OpenAI response did not include an assistant message");

messages.push(assistantMessage);

for (const toolCall of assistantMessage.tool_calls ?? []) {
  let toolResult: unknown;

  try {
    toolResult = await captureTool(toolCall, async () => {
      const args = JSON.parse(toolCall.function.arguments || "{}") as { city?: string };
      return getWeather(args.city ?? "");
    });
  } catch (error) {
    if (!(error instanceof AdrianPolicyBlockedError)) throw error;
    toolResult = BLOCKED_TOOL_MESSAGE;
  }

  messages.push({
    role: "tool",
    tool_call_id: toolCall.id,
    content: typeof toolResult === "string" ? toolResult : JSON.stringify(toolResult),
  });
}

await shutdown();
```

### Responses API

```ts
const response = await openai.responses.create({
  model: "gpt-4o-mini",
  input: "Summarize the security considerations for this workflow.",
});

console.log(response.output_text);
```

### Streaming

Streaming calls are passed through unchanged. Adrian emits one paired event when the stream finishes or the consumer exits early:

```ts
const stream = await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Write a short haiku." }],
  stream: true,
});

for await (const chunk of stream) {
  process.stdout.write(chunk.choices[0]?.delta?.content ?? "");
}
```

### Local logging only

Use `wsUrl: null` when you want JSONL logging without connecting to the Adrian backend:

```ts
await init({
  wsUrl: null,
  logFile: "events.jsonl",
  onEvent: (eventType, data, runId, parentRunId, eventId) => {
    console.log({ eventType, runId, parentRunId, eventId, data });
  },
});
```

## Environment variables

| Variable | Description |
|---|---|
| `ADRIAN_API_KEY` | API key from the Adrian dashboard |
| `ADRIAN_LOG_FILE` | Local JSONL log path (default: `events.jsonl`) |
| `ADRIAN_WS_URL` | WebSocket endpoint (default: `ws://localhost:8080/ws`) |
| `ADRIAN_SESSION_ID` | Session identifier for grouping events |
| `ADRIAN_BLOCK_TIMEOUT` | Seconds to wait for a BLOCK/HITL verdict |
| `ADRIAN_REPLAY_BUFFER_FRAMES` | WebSocket replay buffer size |

## Development

```bash
npm install
npm run build
npm test
```

Build or test a single package:

```bash
npm run build -w @secureagentics/adrian
npm test -w @secureagentics/adrian-openai
```

Per-package docs: [core](./packages/core) · [openai](./packages/openai)
