import type { CallbackMetadata, ChatMessage } from "./types.js";

const CHECKPOINT_NS_KEY = "langgraph_checkpoint_ns";

export function isLangGraphMetadata(metadata: CallbackMetadata): boolean {
  return CHECKPOINT_NS_KEY in metadata;
}

export function deriveLangGraphAgentId(metadata: CallbackMetadata): string | null {
  const ns = metadata[CHECKPOINT_NS_KEY];
  if (typeof ns !== "string" || ns.length === 0) return null;
  const nodeNames = ns.split("|").map((segment) => segment.split(":")[0]).filter(Boolean);
  return nodeNames.length > 0 ? nodeNames.join("|") : null;
}

export function deriveAgentId(metadata: CallbackMetadata | null, _messages?: ChatMessage[] | null): string {
  if (metadata) {
    const langGraphId = deriveLangGraphAgentId(metadata);
    if (langGraphId !== null) return langGraphId;
  }
  return "default";
}
