// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import (
	"net/http"
	"time"

	"github.com/secureagentics/Adrian/backend/internal/store"
)

type verdictResponse struct {
	ID             string `json:"id"`
	EventID        string `json:"event_id"`
	SessionID      string `json:"session_id"`
	MADCode        string `json:"mad_code"`
	Classification string `json:"classification"`
	VerdictStatus  string `json:"verdict_status"`
	LatencyMS      *int64 `json:"latency_ms,omitempty"`
	TokensUsed     int32  `json:"tokens_used"`
	CreatedAt      string `json:"created_at"`
}

type verdictListResponse struct {
	Verdicts []verdictResponse `json:"verdicts"`
	Total    int               `json:"total"`
	Page     int               `json:"page"`
	PerPage  int               `json:"per_page"`
}

func (s *Server) handleListVerdicts(w http.ResponseWriter, r *http.Request) {
	pg := parsePagination(r)
	q := r.URL.Query()

	since := time.Now().Add(-24 * time.Hour)
	if v := q.Get("since"); v != "" {
		if t, err := time.Parse(time.RFC3339, v); err == nil {
			since = t
		}
	}
	if c := q.Get("classification"); c != "" && !validVerdictClassification(c) {
		writeError(w, http.StatusBadRequest, "invalid classification")
		return
	}
	if status := q.Get("verdict_status"); status != "" && !validVerdictStatus(status) {
		writeError(w, http.StatusBadRequest, "invalid verdict_status")
		return
	}
	filters := store.VerdictFilters{
		Since:          since,
		Classification: q.Get("classification"),
		MADCode:        q.Get("mad_code"),
		VerdictStatus:  q.Get("verdict_status"),
	}
	rows, total, err := s.store.ListVerdicts(r.Context(), filters, pg.PerPage, pg.Offset)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := verdictListResponse{
		Verdicts: make([]verdictResponse, 0, len(rows)),
		Total:    total,
		Page:     pg.Page,
		PerPage:  pg.PerPage,
	}
	for _, row := range rows {
		resp.Verdicts = append(resp.Verdicts, verdictRowToResponse(row))
	}
	writeJSON(w, http.StatusOK, resp)
}

func verdictRowToResponse(r *store.VerdictListRow) verdictResponse {
	return verdictResponse{
		ID:             r.ID,
		EventID:        r.EventID,
		SessionID:      r.SessionID,
		MADCode:        r.MADCode,
		Classification: r.Classification,
		VerdictStatus:  r.VerdictStatus,
		LatencyMS:      r.LatencyMS,
		TokensUsed:     r.TokensUsed,
		CreatedAt:      r.CreatedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
	}
}

func validVerdictClassification(c string) bool {
	switch c {
	case "benign", "notify", "block", "error":
		return true
	}
	return false
}

func validVerdictStatus(s string) bool {
	switch s {
	case "ok", "error":
		return true
	}
	return false
}
