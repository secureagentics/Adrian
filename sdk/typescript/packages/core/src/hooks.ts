import type { PairedEvent } from "./format/types.js";
import type { EventHandler } from "./types.js";

export class HookRegistry {
  private handlers: EventHandler[] = [];

  register(handler: EventHandler): void {
    this.handlers.push(handler);
  }

  get size(): number {
    return this.handlers.length;
  }

  async emit(event: PairedEvent): Promise<void> {
    for (const handler of this.handlers) {
      try {
        await handler.onPairedEvent(event);
      } catch (error) {
        console.error("adrian handler failed", { handler: handler.constructor?.name, eventId: event.eventId, error });
      }
    }
  }

  async close(): Promise<void> {
    for (const handler of this.handlers) {
      try {
        await handler.close();
      } catch (error) {
        console.error("adrian handler close failed", { handler: handler.constructor?.name, error });
      }
    }
  }
}
