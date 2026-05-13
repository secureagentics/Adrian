// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import (
	"errors"
	"net/http"

	"github.com/go-chi/chi/v5"

	"github.com/secureagentics/Adrian/backend/internal/store"
)

type agentEntry struct {
	ID               string `json:"id"`
	AgentID          string `json:"agent_id"`
	AgentProfileID   string `json:"agent_profile_id"`
	AgentProfileName string `json:"agent_profile_name"`
	FirstSeen        string `json:"first_seen"`
	LastSeen         string `json:"last_seen"`
	EventCount       int    `json:"event_count"`
	WorstMAD         string `json:"worst_mad"`
}

type agentListResponse struct {
	Agents  []agentEntry `json:"agents"`
	Total   int          `json:"total"`
	Page    int          `json:"page"`
	PerPage int          `json:"per_page"`
}

type agentSessionEntry struct {
	SessionID  string `json:"session_id"`
	StartedAt  string `json:"started_at"`
	EndedAt    string `json:"ended_at"`
	EventCount int    `json:"event_count"`
}

type agentDetailResponse struct {
	agentEntry
	Sessions []agentSessionEntry `json:"sessions"`
}

func (s *Server) handleListAgents(w http.ResponseWriter, r *http.Request) {
	pg := parsePagination(r)
	rows, total, err := s.store.ListAgents(r.Context(), pg.PerPage, pg.Offset)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := agentListResponse{
		Agents:  make([]agentEntry, 0, len(rows)),
		Total:   total,
		Page:    pg.Page,
		PerPage: pg.PerPage,
	}
	for _, row := range rows {
		resp.Agents = append(resp.Agents, agentRowToEntry(row))
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleGetAgent(w http.ResponseWriter, r *http.Request) {
	agentID := chi.URLParam(r, "agent_id")
	row, sessions, err := s.store.GetAgent(r.Context(), agentID)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, http.StatusNotFound, "agent not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := agentDetailResponse{
		agentEntry: agentRowToEntry(row),
		Sessions:   make([]agentSessionEntry, 0, len(sessions)),
	}
	for _, sess := range sessions {
		resp.Sessions = append(resp.Sessions, agentSessionEntry{
			SessionID:  sess.SessionID,
			StartedAt:  sess.StartedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
			EndedAt:    sess.EndedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
			EventCount: sess.EventCount,
		})
	}
	writeJSON(w, http.StatusOK, resp)
}

func agentRowToEntry(row *store.AgentRow) agentEntry {
	return agentEntry{
		ID:               row.ID,
		AgentID:          row.AgentID,
		AgentProfileID:   row.AgentProfileID,
		AgentProfileName: row.AgentProfileName,
		FirstSeen:        row.FirstSeen.UTC().Format("2006-01-02T15:04:05.000Z"),
		LastSeen:         row.LastSeen.UTC().Format("2006-01-02T15:04:05.000Z"),
		EventCount:       row.EventCount,
		WorstMAD:         row.WorstMAD,
	}
}
