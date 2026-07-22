// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import type { PairedEvent } from "../format/types.js";
import type { EventHandler, JsonValue } from "../types.js";
import { redactText, type PiiConfig } from "./engine.js";
import { detect } from "./patterns.js";

export class PiiRedactor {
  private config: PiiConfig;
  constructor(config: PiiConfig = {}) {
    this.config = config;
  }

  redactEvent(event: PairedEvent, options: { inPlace?: boolean } = {}): PairedEvent {
    const target = options.inPlace ? event : structuredClone(event);
    target.agent.systemPrompt = this.redactString(target.agent.systemPrompt);
    target.agent.userInstruction = this.redactString(target.agent.userInstruction);
    if (target.parent) {
      target.parent.systemPrompt = this.redactString(target.parent.systemPrompt);
      target.parent.userInstruction = this.redactString(target.parent.userInstruction);
    }
    if (target.data.kind === "llm") {
      for (const message of target.data.messages) message.content = this.redactString(message.content);
      target.data.output = this.redactString(target.data.output);
      for (const call of target.data.toolCalls) call.args = this.redactValue(call.args) as typeof call.args;
    } else {
      target.data.input = this.redactString(target.data.input);
      target.data.output = this.redactString(target.data.output);
    }
    return target;
  }

  eventHasPii(event: PairedEvent): boolean {
    const enabledTypes = Array.isArray(this.config.enabledTypes) ? new Set(this.config.enabledTypes) : this.config.enabledTypes ?? null;
    return this.iterEventText(event).some((text) => detect(text, enabledTypes).length > 0);
  }

  private redactString(text: string): string {
    return redactText(text, this.config).text;
  }

  private redactValue(value: JsonValue): JsonValue {
    if (typeof value === "string") return this.redactString(value);
    if (Array.isArray(value)) return value.map((item) => this.redactValue(item));
    if (value && typeof value === "object") return Object.fromEntries(Object.entries(value).map(([key, val]) => [key, this.redactValue(val)]));
    return value;
  }

  private iterEventText(event: PairedEvent): string[] {
    const texts = [event.agent.systemPrompt, event.agent.userInstruction];
    if (event.parent) texts.push(event.parent.systemPrompt, event.parent.userInstruction);
    if (event.data.kind === "llm") {
      texts.push(...event.data.messages.map((msg) => msg.content), event.data.output);
      for (const call of event.data.toolCalls) collectStrings(call.args, texts);
    } else {
      texts.push(event.data.input, event.data.output);
    }
    return texts;
  }
}

export class RedactingHandler implements EventHandler {
  private inner: EventHandler;
  private redactor: PiiRedactor;
  constructor(inner: EventHandler, config: PiiConfig = {}) {
    this.inner = inner;
    this.redactor = new PiiRedactor(config);
  }

  async onPairedEvent(event: PairedEvent): Promise<void> {
    const next = this.redactor.eventHasPii(event) ? this.redactor.redactEvent(event) : event;
    await this.inner.onPairedEvent(next);
  }

  async close(): Promise<void> {
    await this.inner.close();
  }
}

function collectStrings(value: JsonValue, sink: string[]): void {
  if (typeof value === "string") sink.push(value);
  else if (Array.isArray(value)) value.forEach((item) => collectStrings(item, sink));
  else if (value && typeof value === "object") Object.values(value).forEach((item) => collectStrings(item, sink));
}
