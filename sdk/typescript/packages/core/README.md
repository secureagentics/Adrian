# @secureagentics/adrian

Core SDK for [Adrian](https://github.com/secureagentics/Adrian) multi-agent security monitoring in Node.js.

This package provides the shared runtime: event capture and pairing, agent/invocation context, PII redaction, handlers, and the WebSocket transport to the Adrian backend. Provider-specific instrumentation ships as separate packages (for example [`@secureagentics/adrian-openai`](https://www.npmjs.com/package/@secureagentics/adrian-openai)).

## Install

```bash
npm install @secureagentics/adrian
```

## Usage

```ts
import { adrian } from "@secureagentics/adrian";

await adrian.init({
  apiKey: "adr_...",
  sessionId: "my-session",
  // wsUrl: "ws://localhost:8080/ws", // self-hosted backend
});
```

To instrument the OpenAI SDK, install [`@secureagentics/adrian-openai`](https://www.npmjs.com/package/@secureagentics/adrian-openai) and wrap your client with `adrian.openai(...)`.

Full documentation: [Adrian TypeScript SDK](https://github.com/secureagentics/Adrian/tree/main/sdk/typescript#readme)

## License

Apache-2.0
