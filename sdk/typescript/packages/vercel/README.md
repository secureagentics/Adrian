# @secureagentics/adrian-vercel

Vercel AI SDK instrumentation for Adrian security monitoring. Includes `@secureagentics/adrian` as a dependency — no separate core install needed.

## Install

```bash
npm install @secureagentics/adrian-vercel ai
```

```ts
import { init, adrian, adrianTools, captureTool } from "@secureagentics/adrian-vercel";
```

## Usage

```ts
import * as ai from "ai";
import { init, shutdown, adrian, adrianTools, captureTool } from "@secureagentics/adrian-vercel";

await init({ apiKey: process.env.ADRIAN_API_KEY });

const ai = adrian(ai);

await ai.generateText({
  model,
  prompt: "Hello",
});

await shutdown();
```

`adrian()` wraps `generateText`, `streamText`, `generateObject`, and `streamObject`.

With **BLOCK** or **HITL** policy and a live WebSocket, `generateText` / streaming calls wait for verdicts when the model returns tool calls, and `captureTool` / `adrianTools` wait before running `execute`. A halt throws `AdrianPolicyBlockedError`.

## Tools

Pass wrapped tool definitions to `generateText`:

```ts
const result = await ai.generateText({
  model,
  prompt: "Use the weather tool.",
  tools: adrianTools(tools),
});
```

`adrian()` also accepts a plain tools object directly (auto-detected when every key has `execute` or `description`):

```ts
const tools = adrian({
  getWeather: {
    description: "Get weather for a city",
    execute: async ({ city }) => ({ city, temp: 72 }),
  },
});

await tools.getWeather.execute({ city: "London" });
```

## Manual tool capture

When you execute Vercel AI tool calls yourself:

```ts
for (const toolCall of result.toolCalls ?? []) {
  await captureTool(toolCall, () => runTool(toolCall.toolName, toolCall.args));
}
```

## API


| Export                                     | Description                                    |
| ------------------------------------------ | ---------------------------------------------- |
| `init(options?)`                           | Initialise Adrian (re-exported from core)      |
| `shutdown()`                               | Tear down Adrian (re-exported from core)       |
| `adrian(target, options?)`                 | Wrap a Vercel AI module or tools object        |
| `adrianTools(tools, options?)`             | Wrap tool definitions passed to `generateText` |
| `captureTool(toolCall, execute, options?)` | Capture manual tool execution                  |


### Types


| Type                 | Fields                              |
| -------------------- | ----------------------------------- |
| `AdrianOptions`      | `metadata?`                         |
| `ToolCallLike`       | `toolCallId`, `toolName`, `args`, … |
| `ToolCaptureOptions` | `metadata?`, `parentRunId?`         |


See the [workspace README](../README.md) for environment variables and multi-provider setup.
