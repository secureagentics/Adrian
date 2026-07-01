// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	"github.com/secureagentics/Adrian/backend/internal/store"
)

// session is per-connection state. Created at WS upgrade time, populated
// from the LoginAck round-trip, consumed by the read loop.
type session struct {
	apiKey      *store.APIKey
	sessionID   string
	llmProvider string
	llmModel    string
	loggedIn    bool

	warnedClassifierErrorCompatibility bool
}

// agentProfileID returns the bound agent_profile_id (or nil if the
// API key has none). Threaded into the events / verdicts inserts.
func (s *session) agentProfileID() *string {
	if s.apiKey == nil {
		return nil
	}
	return s.apiKey.AgentProfileID
}

// routeOwner returns the server-authenticated logical owner for hub routing.
// Agent-profile keys may rotate, so profile ownership is preferred over raw
// key ID to preserve reconnect continuity. Unprofiled keys fall back to key ID.
func (s *session) routeOwner() string {
	if s.apiKey == nil {
		return ""
	}
	if s.apiKey.AgentProfileID != nil && *s.apiKey.AgentProfileID != "" {
		return "agent_profile:" + *s.apiKey.AgentProfileID
	}
	return "api_key:" + s.apiKey.ID
}
