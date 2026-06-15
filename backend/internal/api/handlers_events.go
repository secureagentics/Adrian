// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import (
	"encoding/json"
	"errors"
	"net/http"
	"time"

	"github.com/go-chi/chi/v5"

	"github.com/secureagentics/Adrian/backend/internal/store"
)

type eventResponse struct {
	ID             string          `json:"id"`
	SessionID      string          `json:"session_id"`
	AgentID        string          `json:"agent_id"`
	AgentName      string          `json:"agent_name"`
	AgentProfileID *string         `json:"agent_profile_id"`
	EventType      string          `json:"event_type"`
	RunID          string          `json:"run_id"`
	Payload        json.RawMessage `json:"payload"`
	TokensUsed     int32           `json:"tokens_used"`
	CreatedAt      string          `json:"created_at"`
}

type eventListResponse struct {
	Events  []eventResponse `json:"events"`
	Total   int             `json:"total"`
	Page    int             `json:"page"`
	PerPage int             `json:"per_page"`
}

type eventDetailResponse struct {
	eventResponse
	Verdict *verdictResponse `json:"verdict,omitempty"`
}

type timelineVerdict struct {
	ID             string `json:"id"`
	MADCode        string `json:"mad_code"`
	Classification string `json:"classification"`
	VerdictStatus  string `json:"verdict_status"`
}

type timelineEntry struct {
	ID        string           `json:"id"`
	EventType string           `json:"event_type"`
	RunID     string           `json:"run_id"`
	AgentID   string           `json:"agent_id"`
	AgentName string           `json:"agent_name"`
	Payload   json.RawMessage  `json:"payload"`
	CreatedAt string           `json:"created_at"`
	Verdict   *timelineVerdict `json:"verdict,omitempty"`
}

type sessionTimelineResponse struct {
	SessionID string          `json:"session_id"`
	Entries   []timelineEntry `json:"entries"`
}

func (s *Server) handleListEvents(w http.ResponseWriter, r *http.Request) {
	pg := parsePagination(r)
	q := r.URL.Query()

	since := time.Now().Add(-24 * time.Hour)
	if v := q.Get("since"); v != "" {
		if t, err := time.Parse(time.RFC3339, v); err == nil {
			since = t
		}
	}
	if status := q.Get("verdict_status"); status != "" && !validVerdictStatus(status) {
		writeError(w, http.StatusBadRequest, "invalid verdict_status")
		return
	}

	filters := store.EventFilters{
		Since:         since,
		AgentID:       q.Get("agent_id"),
		SessionID:     q.Get("session_id"),
		EventType:     q.Get("event_type"),
		MinMAD:        q.Get("min_mad"),
		VerdictStatus: q.Get("verdict_status"),
	}

	rows, total, err := s.store.ListEvents(r.Context(), filters, pg.PerPage, pg.Offset)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}

	resp := eventListResponse{
		Events:  make([]eventResponse, 0, len(rows)),
		Total:   total,
		Page:    pg.Page,
		PerPage: pg.PerPage,
	}
	for _, row := range rows {
		resp.Events = append(resp.Events, eventToResponse(row))
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleGetEvent(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	row, err := s.store.GetEvent(r.Context(), id)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, http.StatusNotFound, "event not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}

	resp := eventDetailResponse{eventResponse: eventToResponse(row)}
	if v, err := s.store.GetVerdictByEventID(r.Context(), id); err == nil {
		vr := verdictRowToResponse(v)
		resp.Verdict = &vr
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleSessionTimeline(w http.ResponseWriter, r *http.Request) {
	sessionID := chi.URLParam(r, "session_id")
	rows, err := s.store.SessionTimeline(r.Context(), sessionID)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := sessionTimelineResponse{
		SessionID: sessionID,
		Entries:   make([]timelineEntry, 0, len(rows)),
	}
	for _, row := range rows {
		entry := timelineEntry{
			ID:        row.ID,
			EventType: row.EventType,
			RunID:     row.RunID,
			AgentID:   row.AgentID,
			AgentName: row.AgentName,
			Payload:   json.RawMessage(row.PayloadJSON),
			CreatedAt: row.CreatedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
		}
		if len(entry.Payload) == 0 {
			entry.Payload = json.RawMessage("null")
		}
		if row.VerdictID != "" {
			entry.Verdict = &timelineVerdict{
				ID:             row.VerdictID,
				MADCode:        row.MADCode,
				Classification: row.Classification,
				VerdictStatus:  row.VerdictStatus,
			}
		}
		resp.Entries = append(resp.Entries, entry)
	}
	writeJSON(w, http.StatusOK, resp)
}

func eventToResponse(r *store.EventListRow) eventResponse {
	resp := eventResponse{
		ID:             r.ID,
		SessionID:      r.SessionID,
		AgentID:        r.AgentID,
		AgentName:      r.AgentName,
		AgentProfileID: r.AgentProfileID,
		EventType:      r.EventType,
		RunID:          r.RunID,
		Payload:        json.RawMessage(r.PayloadJSON),
		TokensUsed:     r.TokensUsed,
		CreatedAt:      r.CreatedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
	}
	if len(resp.Payload) == 0 {
		resp.Payload = json.RawMessage("null")
	}
	return resp
}
