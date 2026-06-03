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

## Tool execution capture

OpenAI returns tool call requests; your app executes the tools. Wrap that execution with `captureTool`:

```ts
for (const toolCall of assistantMessage.tool_calls ?? []) {
  const toolResult = await captureTool(toolCall, () =>
    runTool(toolCall.function.name, toolCall.function.arguments),
  );

  messages.push({
    role: "tool",
    tool_call_id: toolCall.id,
    content: toolResult,
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

### Types

| Type | Fields |
|---|---|
| `AdrianOptions` | `metadata?` |
| `ToolCallLike` | `id`, `function.name`, `function.arguments`, … |
| `ToolCaptureOptions` | `metadata?`, `parentRunId?` |

See the [workspace README](../README.md) for environment variables and multi-provider setup.
