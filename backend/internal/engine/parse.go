// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package engine

import (
	"regexp"
	"strings"
)

// madCodePattern matches MAD codes in the classifier's response.
// Accepts the canonical taxonomy: M0, M2.a-z, M3.a-z, M4.a-z, plus
// the bare base codes (M0, M2, M3, M4). Rejects M1 / M5 (not in
// taxonomy), uppercase letters, and underscore separators.
var madCodePattern = regexp.MustCompile(`(M[0234](?:\.[a-z])?)`)

// parseMADCode extracts the first valid MAD code from response text.
// Returns "" if no match.
func parseMADCode(response string) string {
	m := madCodePattern.FindStringSubmatch(strings.TrimSpace(response))
	if len(m) < 2 {
		return ""
	}
	return m[1]
}

// stripReasoning removes <reasoning>...</reasoning> blocks from model
// output before parseMADCode runs. Reasoning models
// emit their thinking in those tags; the M-code
// answer follows. Without stripping, parseMADCode can match an
// M-code mention inside the reasoning block and return the wrong
// answer.
func stripReasoning(content string) string {
	for {
		open := strings.Index(content, "<reasoning>")
		if open < 0 {
			return content
		}
		close := strings.Index(content, "</reasoning>")
		if close < 0 {
			// Open without close: drop everything from <reasoning>
			// onwards so the parser sees a clean window rather than
			// matching an M-code inside the dangling thought.
			return content[:open]
		}
		content = content[:open] + content[close+len("</reasoning>"):]
	}
}

// madCodeToClassification maps a classifier-produced M-code to its
// display classification. Empty or unknown codes are operational
// classifier errors, not benign results.
func madCodeToClassification(code string) string {
	if len(code) < 2 {
		return "error"
	}
	switch code[:2] {
	case "M0":
		return "benign"
	case "M2":
		return "notify"
	case "M3", "M4":
		return "block"
	default:
		return "error"
	}
}
