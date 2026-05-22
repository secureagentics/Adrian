# @secureagentics/adrian

TypeScript SDK for Adrian multi-agent event capture in Node.js LangChain.js / LangGraph.js applications.

```ts
import { init } from "@secureagentics/adrian";

await init({ apiKey: process.env.ADRIAN_API_KEY });
```

The SDK mirrors the Python package: it pairs LLM/tool callbacks into `PairedEvent` objects, redacts PII, writes JSONL locally, streams protobuf frames to the Adrian WebSocket endpoint, tracks MCP servers, and applies BLOCK/HITL tool gating when LangGraph ToolNode instrumentation is available.

## Environment

- `ADRIAN_API_KEY`
- `ADRIAN_LOG_FILE`
- `ADRIAN_WS_URL`
- `ADRIAN_SESSION_ID`
- `ADRIAN_BLOCK_TIMEOUT`
- `ADRIAN_REPLAY_BUFFER_FRAMES`

## Manual Callback Wiring

```ts
import { init, getHandler } from "@secureagentics/adrian";

await init({ autoInstrument: false });
const handler = getHandler();
```
