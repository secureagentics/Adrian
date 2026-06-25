# @secureagentics/adrian-openai

OpenAI SDK instrumentation for [Adrian](https://github.com/secureagentics/Adrian) security monitoring. Wraps your OpenAI client so every call is captured by the [core SDK](https://www.npmjs.com/package/@secureagentics/adrian) and streamed to your backend.

## Install

```bash
npm install @secureagentics/adrian-openai openai
```

## Usage

Wrap your OpenAI client. `init`, `adrian.openai(client)`, and `shutdown` bracket your normal OpenAI code; call sites stay unchanged:

```ts
import OpenAI from "openai";
import { adrian } from "@secureagentics/adrian-openai";

async function main() {
  await adrian.init({ apiKey: "adr_local_..." });

  // Wrap your existing OpenAI client; every call is captured.
  const client = adrian.openai(new OpenAI());

  const response = await client.chat.completions.create({
    model: "gpt-4o",
    messages: [
      { role: "user", content: "Find the most underpriced recent IPOs and build an investment strategy" },
    ],
  });
  console.log(response.choices[0]?.message?.content);

  await adrian.shutdown();
}

main();
```

<sup>Requires the `openai` package (peer dependency `>=4.0.0`). The SDK defaults to `ws://localhost:8080/ws`; set `wsUrl=` if your self-hosted backend runs elsewhere.</sup>

Events appear in the dashboard within seconds, classified by severity.

## Local development

To develop against a local build instead of the published package, point your consumer's `package.json` at the package directories with `file:` paths (relative to that file), then `npm install`:

```jsonc
"dependencies": {
  "@secureagentics/adrian":        "file:../Adrian/sdk/typescript/packages/core",
  "@secureagentics/adrian-openai": "file:../Adrian/sdk/typescript/packages/openai",
  "openai": ">=4.0.0"
}
```

Both packages are linked because `adrian-openai` depends on `adrian`. The paths above assume your project is a sibling of the `Adrian` repo; adjust the `../` depth to match. Build first so `dist/` exists, and rebuild after editing the SDK:

```sh
cd sdk/typescript && npm run build
```

Full documentation: [Adrian TypeScript SDK](https://github.com/secureagentics/Adrian/tree/main/sdk/typescript#readme)

## License

Apache-2.0
