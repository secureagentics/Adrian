// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import type { CallbackMetadata, ChatMessage } from "./types.js";

export function deriveAgentId(_metadata: CallbackMetadata | null, _messages?: ChatMessage[] | null): string {
  return "default";
}
