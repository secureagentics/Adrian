// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	"encoding/json"
	"testing"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// TestPairedEventToJSONReasoning checks the reasoning field survives the
// proto to JSON conversion, the dashboard and the engine read the
// persisted payload rather than the wire message.
func TestPairedEventToJSONReasoning(t *testing.T) {
	ev := &pb.PairedEvent{
		EventId:  "ev-1",
		PairType: pb.PairType_PAIR_TYPE_LLM,
		Data: &pb.PairedEvent_Llm{
			Llm: &pb.LlmPairData{
				Model:     "claude-opus-5",
				Output:    "Refund issued.",
				Reasoning: "Order is within the refund window.",
			},
		},
	}
	payload, err := pairedEventToJSON(ev)
	if err != nil {
		t.Fatalf("pairedEventToJSON: %v", err)
	}
	var got struct {
		LLM struct {
			Output    string `json:"output"`
			Reasoning string `json:"reasoning"`
		} `json:"llm"`
	}
	if err := json.Unmarshal([]byte(payload), &got); err != nil {
		t.Fatalf("unmarshal payload: %v", err)
	}
	if got.LLM.Reasoning != "Order is within the refund window." {
		t.Errorf("reasoning = %q, want it persisted", got.LLM.Reasoning)
	}
	if got.LLM.Output != "Refund issued." {
		t.Errorf("output = %q, want it left alone", got.LLM.Output)
	}
}
