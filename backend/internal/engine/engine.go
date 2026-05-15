// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// Package engine is the in-process AI engine that classifies paired
// SDK events. Builds the prompt, calls ADRIAN_LLM_URL, and parses the
// M-code response.
package engine

import (
	"context"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// Verdict is the result of one classification. Mirrors the columns on
// the `verdicts` table.
type Verdict struct {
	MADCode        string
	Classification string
	Reasoning      string
	LatencyMS      int64
}

// Classifier classifies a paired event. Implementations honour ctx
// cancellation. A returned error means classification could not be
// completed safely (LLM unreachable, malformed response, no parseable
// M-code) and the caller must fail closed per execution mode. A nil
// verdict with nil error is not a valid response.
//
// agentProfileID is the customer-facing agent identity bound to the
// SDK's API key (looked up server-side at WS-login time). Pass "" to
// classify against the generic remit; non-empty values trigger an
// agent-profile lookup so the system prompt is rendered with the
// user's remit + custom M0/M3 entries.
type Classifier interface {
	Classify(ctx context.Context, ev *pb.PairedEvent, agentProfileID string) (*Verdict, error)
	// Ping verifies the classifier's upstream is reachable. Used by
	// /readyz to decide whether the backend can serve classification
	// traffic. Implementations should return quickly (independent of
	// classifyTimeout) and must NOT consume model tokens. nil means
	// reachable; any error means the upstream is down or wedged.
	Ping(ctx context.Context) error
}
