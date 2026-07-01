// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// strPtrOrNil returns &s for non-empty input, nil otherwise.
// Useful when feeding nullable TEXT columns without dropping a struct
// literal mid-call.
func strPtrOrNil(s string) *string {
	if s == "" {
		return nil
	}
	return &s
}

// int64PtrIfNonZero returns &n for non-zero input, nil otherwise.
// Same idea as strPtrOrNil, for nullable INTEGER columns.
func int64PtrIfNonZero(n int64) *int64 {
	if n == 0 {
		return nil
	}
	return &n
}

// isActionable reports whether the SDK has a wait point for this event.
// Only LLM pairs that emit tool_calls are gated by the patched
// ToolNode.ainvoke; the matching ToolNode invocation will await the
// LLM's verdict before deciding whether to run the tool.
//
// Tool pairs (post-execution), LLM pairs without tool_calls, and any
// other event type produce verdicts the SDK never blocks on. Queueing
// those for HITL review puts a row in front of the operator that
// approving / rejecting cannot influence, the side effect has either
// already happened or never had one to gate. The /events page still
// surfaces them for audit.
func isActionable(ev *pb.PairedEvent) bool {
	if ev == nil || ev.PairType != pb.PairType_PAIR_TYPE_LLM {
		return false
	}
	llm := ev.GetLlm()
	return llm != nil && len(llm.ToolCalls) > 0
}

// shouldFanOut decides whether an OK verdict's MAD code is in scope
// for the active policy. False for codes outside the M0/M2/M3/M4 set
// (defensive: an unrecognised code drops rather than panics) and for
// MAD families whose policy_mX flag is unset.
//
// The HITL gate uses this to decide whether to hold the verdict; the
// alert / block paths route on it the same way.
func shouldFanOut(snap *pb.PolicySnapshot, madCode string) bool {
	if snap == nil || len(madCode) < 2 {
		return false
	}
	switch madCode[:2] {
	case "M0":
		return snap.PolicyM0
	case "M2":
		return snap.PolicyM2
	case "M3":
		return snap.PolicyM3
	case "M4":
		return snap.PolicyM4
	}
	return false
}
