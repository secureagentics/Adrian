import { AsyncLocalStorage } from "node:async_hooks";
import type { AgentContext, ParentContext } from "./format/types.js";

const invocationStorage = new AsyncLocalStorage<string>();

export function getInvocationId(): string | null {
  return invocationStorage.getStore() ?? null;
}

export function runWithInvocationId<T>(invocationId: string, fn: () => T): T {
  return invocationStorage.run(invocationId, fn);
}

export class AgentContextTracker {
  private contexts = new Map<string, AgentContext>();
  private parentMap = new Map<string, ParentContext | null>();
  private delegatedBy: string | null = null;

  markDelegated(agentId: string): void {
    this.delegatedBy = agentId;
  }

  update(agentId: string, systemPrompt: string, userInstruction: string): ParentContext | null {
    this.contexts.set(agentId, { agentId, systemPrompt, userInstruction });

    if (!this.parentMap.has(agentId)) {
      let parent: ParentContext | null = null;
      if (this.delegatedBy !== null) {
        const previous = this.contexts.get(this.delegatedBy);
        if (previous && previous.agentId !== agentId) {
          parent = { ...previous };
        }
      }

      if (parent === null) {
        const newParts = normalize(agentId.split("|"));
        let bestCandidate: string | null = null;
        let bestCommon = 0;
        for (const otherId of this.contexts.keys()) {
          if (otherId === agentId) continue;
          const otherParts = normalize(otherId.split("|"));
          if (otherParts.length >= newParts.length) continue;
          let common = 0;
          for (let idx = 0; idx < Math.min(otherParts.length, newParts.length); idx += 1) {
            if (otherParts[idx] !== newParts[idx]) break;
            common += 1;
          }
          if (common > bestCommon) {
            bestCommon = common;
            bestCandidate = otherId;
          }
        }
        if (bestCandidate !== null && bestCommon > 0) {
          const previous = this.contexts.get(bestCandidate);
          if (previous) parent = { ...previous };
        }
      }

      this.parentMap.set(agentId, parent);
    }

    if (agentId === this.delegatedBy) {
      this.delegatedBy = null;
    }

    return this.parentMap.get(agentId) ?? null;
  }

  getParent(agentId: string): ParentContext | null {
    return this.parentMap.get(agentId) ?? null;
  }

  hasContext(agentId: string): boolean {
    return this.contexts.has(agentId);
  }

  getContext(agentId: string): AgentContext | null {
    return this.contexts.get(agentId) ?? null;
  }
}

function normalize(parts: string[]): string[] {
  return parts.filter((part) => !/^\d+$/.test(part));
}
