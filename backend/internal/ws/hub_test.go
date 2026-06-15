// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	"errors"
	"testing"

	"google.golang.org/protobuf/proto"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

func TestHubPublishDeliversToSubscriber(t *testing.T) {
	h := NewHub()
	ch, dereg, err := h.Register("sess-1", "owner-1")
	if err != nil {
		t.Fatalf("Register: %v", err)
	}
	defer dereg()

	frame := &pb.ServerFrame{Frame: &pb.ServerFrame_Verdict{Verdict: &pb.Verdict{
		EventId: "ev-1", SessionId: "sess-1", MadCode: "M3",
	}}}
	if !h.Publish("sess-1", frame) {
		t.Fatal("expected Publish to return true")
	}
	got := <-ch
	var decoded pb.ServerFrame
	if err := proto.Unmarshal(got, &decoded); err != nil {
		t.Fatalf("unmarshal: %v", err)
	}
	v := decoded.GetVerdict()
	if v == nil || v.EventId != "ev-1" || v.MadCode != "M3" {
		t.Fatalf("unexpected frame: %+v", decoded.Frame)
	}
}

func TestHubPublishNoSubscriberReturnsFalse(t *testing.T) {
	h := NewHub()
	ok := h.Publish("ghost", &pb.ServerFrame{
		Frame: &pb.ServerFrame_Verdict{Verdict: &pb.Verdict{}},
	})
	if ok {
		t.Fatal("expected Publish to return false when no subscriber")
	}
}

func TestHubReRegisterClosesPriorChannel(t *testing.T) {
	h := NewHub()
	first, _, err := h.Register("sess-x", "owner-1")
	if err != nil {
		t.Fatalf("first Register: %v", err)
	}

	// New register replaces the slot; the old channel must close so a
	// writer goroutine reading from it exits cleanly.
	second, dereg, err := h.Register("sess-x", "owner-1")
	if err != nil {
		t.Fatalf("second Register: %v", err)
	}
	defer dereg()

	if _, ok := <-first; ok {
		t.Fatal("expected first channel closed after re-Register")
	}

	if !h.Publish("sess-x", &pb.ServerFrame{
		Frame: &pb.ServerFrame_Verdict{Verdict: &pb.Verdict{EventId: "ev-y"}},
	}) {
		t.Fatal("Publish to second subscriber should succeed")
	}
	got := <-second
	if len(got) == 0 {
		t.Fatal("expected non-empty frame delivered to second subscriber")
	}
}

func TestHubRejectsReRegisterFromDifferentOwner(t *testing.T) {
	h := NewHub()
	first, dereg, err := h.Register("sess-x", "owner-1")
	if err != nil {
		t.Fatalf("first Register: %v", err)
	}
	defer dereg()

	second, secondDereg, err := h.Register("sess-x", "owner-2")
	if !errors.Is(err, ErrSessionOwnerConflict) {
		t.Fatalf("Register err = %v, want ErrSessionOwnerConflict", err)
	}
	if second != nil || secondDereg != nil {
		t.Fatal("conflicting Register returned a subscriber")
	}

	if !h.Publish("sess-x", &pb.ServerFrame{
		Frame: &pb.ServerFrame_Verdict{Verdict: &pb.Verdict{EventId: "ev-y"}},
	}) {
		t.Fatal("Publish to original subscriber should still succeed")
	}
	got := <-first
	if len(got) == 0 {
		t.Fatal("expected non-empty frame delivered to original subscriber")
	}
}

func TestHubDeregisterRemovesEntry(t *testing.T) {
	h := NewHub()
	_, dereg, err := h.Register("sess-d", "owner-1")
	if err != nil {
		t.Fatalf("Register: %v", err)
	}
	dereg()
	if h.Publish("sess-d", &pb.ServerFrame{
		Frame: &pb.ServerFrame_Verdict{Verdict: &pb.Verdict{}},
	}) {
		t.Fatal("expected Publish to return false after dereg")
	}
}
