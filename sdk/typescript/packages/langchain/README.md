# @secureagentics/adrian-langchain

LangChain instrumentation for [Adrian](https://github.com/secureagentics/Adrian) security monitoring. Wraps your LangChain models and tools so `invoke()`, `stream()`, `bindTools()`, and tool execution are captured by the [core SDK](https://www.npmjs.com/package/@secureagentics/adrian) and streamed to your backend.

## Install

```bash
npm install @secureagentics/adrian-langchain @langchain/core @langchain/openai zod
```

## Usage

Wrap your LangChain model. `init`, `adrian.langchain(model)`, and `shutdown` bracket your normal LangChain code; call sites stay unchanged:

```ts
import { ChatOpenAI } from "@langchain/openai";
import { HumanMessage } from "@langchain/core/messages";
import { adrian } from "@secureagentics/adrian-langchain";

async function main() {
  await adrian.init({ apiKey: "adr_local_..." });

  // Wrap your existing LangChain model; invoke and stream calls are captured.
  const model = adrian.langchain(
    new ChatOpenAI({
      model: "gpt-4o-mini",
    }),
  );

  const response = await model.invoke([
    new HumanMessage("Summarize the security risks in this deployment plan."),
  ]);
  console.log(response.content);

  await adrian.shutdown();
}

main();
```

<sup>Requires `@langchain/core` (peer dependency `>=0.2.0`). The SDK defaults to `ws://localhost:8080/ws`; set `wsUrl=` if your self-hosted backend runs elsewhere.</sup>

Events appear in the dashboard within seconds, classified by severity.

## Streaming

Use the same wrapped model for streaming responses; the streaming call site stays unchanged:

```ts
const stream = await model.stream("Draft a short incident response checklist.");

for await (const chunk of stream) {
  process.stdout.write(String(chunk.content ?? ""));
}
```

## Tools

Wrap LangChain tools before binding or executing them:

```ts
import { tool } from "@langchain/core/tools";
import { z } from "zod";

const lookupUser = tool(
  async ({ userId }) => {
    return JSON.stringify({ userId, status: "active" });
  },
  {
    name: "lookup_user",
    description: "Look up a user by ID",
    schema: z.object({ userId: z.string() }),
  },
);

const tools = adrian.langchain([lookupUser]);
const modelWithTools = adrian.langchain(model.bindTools(tools));

const response = await modelWithTools.invoke("Check whether user_123 can access production.");
console.log(response.content);

for (const call of response.tool_calls ?? []) {
  const selectedTool = tools.find((candidate) => candidate.name === call.name);
  if (selectedTool) console.log(await selectedTool.invoke(call));
}
```

## Local development

To develop against a local build instead of the published package, point your consumer's `package.json` at the package directories with `file:` paths (relative to that file), then `npm install`:

```jsonc
"dependencies": {
  "@secureagentics/adrian":           "file:../Adrian/sdk/typescript/packages/core",
  "@secureagentics/adrian-langchain": "file:../Adrian/sdk/typescript/packages/langchain",
  "@langchain/core":                  ">=0.2.0"
}
```

Both packages are linked because `adrian-langchain` depends on `adrian`. The paths above assume your project is a sibling of the `Adrian` repo; adjust the `../` depth to match. Build first so `dist/` exists, and rebuild after editing the SDK:

```sh
cd sdk/typescript && npm run build
```

Full documentation: [Adrian TypeScript SDK](https://github.com/secureagentics/Adrian/tree/main/sdk/typescript#readme)

## License

Apache-2.0
