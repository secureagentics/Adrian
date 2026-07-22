// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

export { redactText } from "./engine.js";
export type { PiiConfig, RedactionResult } from "./engine.js";
export { PiiType, detect } from "./patterns.js";
export type { Detection } from "./patterns.js";
export { RedactionStrategy } from "./strategies.js";
export { PiiRedactor, RedactingHandler } from "./redactor.js";
