# @secureagentics/adrian-openai

OpenAI SDK instrumentation for Adrian security monitoring. Includes `@secureagentics/adrian` as a dependency — no separate core install needed.

## Install

```bash
npm install @secureagentics/adrian-openai openai
```

```ts
import { init, adrian, captureTool } from "@secureagentics/adrian-openai";
```

## Usage

```ts
import OpenAI from "openai";
import { init, shutdown, adrian, captureTool } from "@secureagentics/adrian-openai";

await init({ apiKey: process.env.ADRIAN_API_KEY });

const openai = adrian(new OpenAI());

await openai.chat.completions.create({
  model: "gpt-4o-mini",
  messages: [{ role: "user", content: "Hello" }],
});

await shutdown();
```

`adrian()` wraps `chat.completions.create`, `responses.create`, and their streaming variants. Events are paired, PII-redacted, and streamed to Adrian automatically.

When the dashboard policy is in **BLOCK** or **HITL** mode and `init()` is connected over WebSocket, the SDK waits for verdicts on LLM turns that propose tool calls and throws `AdrianPolicyBlockedError` before those tools can run. `captureTool` applies the same gate before executing your handler.

Pass metadata when wrapping the client to attach app-specific context to each captured event:

```ts
const openai = adrian(new OpenAI(), {
  metadata: { environment: "production", service: "checkout-agent" },
});
```

## Responses API

```ts
const response = await openai.responses.create({
  model: "gpt-4o-mini",
  input: "Summarize the security considerations for this workflow.",
});

console.log(response.output_text);
```

## Streaming

Streaming chunks are passed through unchanged. Adrian emits one paired LLM event after the stream completes, or when the consumer exits early:

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

The Responses API streaming path is captured the same way:

```ts
const stream = await openai.responses.create({
  model: "gpt-4o-mini",
  input: "Write a one-sentence project update.",
  stream: true,
});

for await (const event of stream) {
  if (event.type === "response.output_text.delta") process.stdout.write(event.delta);
}
```

## Tool execution capture

OpenAI returns tool call requests; your app executes the tools. Wrap that execution with `captureTool`:

```ts
import { AdrianPolicyBlockedError, BLOCKED_TOOL_MESSAGE } from "@secureagentics/adrian-openai";

async function runTool(name: string, argsJson: string) {
  const args = JSON.parse(argsJson || "{}") as Record<string, unknown>;
  if (name === "get_weather") return { city: args.city, forecast: "sunny" };
  throw new Error(`Unknown tool: ${name}`);
}

for (const toolCall of assistantMessage.tool_calls ?? []) {
  let toolResult: unknown;

  try {
    toolResult = await captureTool(toolCall, () =>
      runTool(toolCall.function.name, toolCall.function.arguments),
    );
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
```

## API

| Export | Description |
|---|---|
| `init(options?)` | Initialise Adrian (re-exported from core) |
| `shutdown()` | Tear down Adrian (re-exported from core) |
| `adrian(client, options?)` | Wrap an OpenAI client |
| `captureTool(toolCall, execute, options?)` | Capture manual tool execution |
| `AdrianPolicyBlockedError` | Error thrown when policy blocks a tool call |
| `BLOCKED_TOOL_MESSAGE` | Standard blocked tool result string |

### Types

| Type | Fields |
|---|---|
| `AdrianOptions` | `metadata?` |
| `ToolCallLike` | `id`, `function.name`, `function.arguments`, … |
| `ToolCaptureOptions` | `metadata?`, `parentRunId?` |

See the [workspace README](../../README.md) for environment variables and multi-provider setup.
