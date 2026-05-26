// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	"errors"
	"sync"

	"google.golang.org/protobuf/proto"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// ErrSessionOwnerConflict is returned when another logical client owner
// already holds the subscriber slot for a session_id.
var ErrSessionOwnerConflict = errors.New("session_id already registered by another owner")

type subscriber struct {
	owner string
	ch    chan []byte
}

// Hub is a process-local pub/sub keyed by session_id. The WS handler
// for each connected SDK registers a write channel; the REST review
// approve/reject path publishes a HITL-resolution Verdict frame to it.
//
// Single subscriber per session_id. Re-register by the same server-derived
// owner replaces the prior channel (SDK reconnect / key rotation). A
// different owner claiming the same session_id is rejected so it cannot steal
// verdict or HITL routing.
type Hub struct {
	mu   sync.Mutex
	subs map[string]subscriber
}

// NewHub returns a fresh hub.
func NewHub() *Hub {
	return &Hub{subs: make(map[string]subscriber)}
}

// Register adds a subscriber for sessionID + owner and returns its write
// channel plus a deregister callback. The caller spawns a writer
// goroutine that drains the channel and calls conn.WriteMessage.
//
// If a prior subscriber exists for the same session_id and owner, its channel
// is closed so its writer goroutine exits cleanly. If the existing subscriber
// belongs to another owner, registration fails and the old channel remains
// active.
func (h *Hub) Register(sessionID, owner string) (<-chan []byte, func(), error) {
	h.mu.Lock()
	defer h.mu.Unlock()

	if old, ok := h.subs[sessionID]; ok {
		if old.owner != owner {
			return nil, nil, ErrSessionOwnerConflict
		}
		close(old.ch)
	}
	ch := make(chan []byte, 8)
	h.subs[sessionID] = subscriber{owner: owner, ch: ch}

	deregister := func() {
		h.mu.Lock()
		defer h.mu.Unlock()
		// Only delete + close when the entry is still ours; a later
		// Register may have replaced it and already closed the prior
		// channel.
		if cur, ok := h.subs[sessionID]; ok && cur.ch == ch {
			delete(h.subs, sessionID)
			close(ch)
		}
	}
	return ch, deregister, nil
}

// Publish marshals and pushes a frame to the subscriber for sessionID.
// Returns true if delivered, false if no subscriber or the channel was
// full (dropped). REST callers log warn on false but still return 200
// to the dashboard, the hitl_queue row is the source of truth.
func (h *Hub) Publish(sessionID string, frame *pb.ServerFrame) bool {
	buf, err := proto.Marshal(frame)
	if err != nil {
		return false
	}
	h.mu.Lock()
	defer h.mu.Unlock()
	sub, ok := h.subs[sessionID]
	if !ok {
		return false
	}
	select {
	case sub.ch <- buf:
		return true
	default:
		return false
	}
}
