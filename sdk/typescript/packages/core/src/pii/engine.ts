// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

import { detect, type Detection, type PiiType } from "./patterns.js";
import { applyStrategy, RedactionStrategy } from "./strategies.js";

export interface PiiConfig {
  strategy?: RedactionStrategy;
  enabledTypes?: ReadonlySet<PiiType> | PiiType[] | null;
}

export interface RedactionResult {
  text: string;
  detections: Detection[];
}

export function redactText(text: string, config: PiiConfig = {}): RedactionResult {
  if (!text) return { text, detections: [] };
  const enabledTypes = Array.isArray(config.enabledTypes) ? new Set(config.enabledTypes) : config.enabledTypes ?? null;
  const detections = detect(text, enabledTypes);
  if (detections.length === 0) return { text, detections };
  let result = text;
  for (const detection of [...detections].reverse()) {
    result = result.slice(0, detection.start) + applyStrategy(detection, config.strategy ?? RedactionStrategy.REPLACE) + result.slice(detection.end);
  }
  return { text: result, detections };
}
