// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import (
	"encoding/json"
	"errors"
	"log/slog"
	"net/http"

	"github.com/go-chi/chi/v5"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

type reviewSummary struct {
	ID            string `json:"id"`
	EventID       string `json:"event_id"`
	VerdictID     string `json:"verdict_id"`
	SessionID     string `json:"session_id"`
	MADCode       string `json:"mad_code"`
	VerdictStatus string `json:"verdict_status"`
	Status        string `json:"status"`
	CreatedAt     string `json:"created_at"`
	ReviewedBy    string `json:"reviewed_by,omitempty"`
	ReviewedAt    string `json:"reviewed_at,omitempty"`
}

type reviewListResponse struct {
	Reviews []reviewSummary `json:"reviews"`
	Total   int             `json:"total"`
	Page    int             `json:"page"`
	PerPage int             `json:"per_page"`
}

type reviewDetail struct {
	reviewSummary
	EventPayload   json.RawMessage `json:"event_payload,omitempty"`
	Classification string          `json:"classification,omitempty"`
	Reasoning      string          `json:"reasoning,omitempty"`
}

type reviewResolveResponse struct {
	Status   string `json:"status"`
	Resolved bool   `json:"resolved"`
	Notice   string `json:"notice,omitempty"`
}

func (s *Server) handleListReviews(w http.ResponseWriter, r *http.Request) {
	pg := parsePagination(r)
	q := r.URL.Query()
	status := q.Get("status")
	verdictStatus := q.Get("verdict_status")
	if verdictStatus != "" && !validVerdictStatus(verdictStatus) {
		writeError(w, http.StatusBadRequest, "invalid verdict_status")
		return
	}
	rows, total, err := s.store.ListHitlQueue(r.Context(), status, verdictStatus, pg.PerPage, pg.Offset)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := reviewListResponse{
		Reviews: make([]reviewSummary, 0, len(rows)),
		Total:   total,
		Page:    pg.Page,
		PerPage: pg.PerPage,
	}
	for _, row := range rows {
		resp.Reviews = append(resp.Reviews, reviewToSummary(row))
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleGetReview(w http.ResponseWriter, r *http.Request) {
	id := chi.URLParam(r, "id")
	row, err := s.store.GetHitlReview(r.Context(), id)
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, http.StatusNotFound, "review not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := reviewDetail{
		reviewSummary:  reviewToSummary(&row.HitlReview),
		Classification: row.Classification,
		Reasoning:      row.Reasoning,
	}
	if row.EventPayloadJSON != "" {
		resp.EventPayload = json.RawMessage(row.EventPayloadJSON)
	}
	writeJSON(w, http.StatusOK, resp)
}

func (s *Server) handleApproveReview(w http.ResponseWriter, r *http.Request) {
	s.resolveReview(w, r, "approved", true)
}

func (s *Server) handleRejectReview(w http.ResponseWriter, r *http.Request) {
	s.resolveReview(w, r, "rejected", false)
}

// resolveReview is the shared approve / reject path. It atomically
// updates the queue row, then publishes a HITL-resolution Verdict
// frame back to the SDK via the WS hub. If no SDK is connected for
// that session_id we still return 200; the queue row is the source of
// truth and the dashboard's UX shouldn't pretend the click failed.
func (s *Server) resolveReview(w http.ResponseWriter, r *http.Request, status string, continueExec bool) {
	id := chi.URLParam(r, "id")
	row, ok, err := s.store.ResolveHitl(r.Context(), id, status, userID(r))
	if err != nil {
		if errors.Is(err, store.ErrNotFound) {
			writeError(w, http.StatusNotFound, "review not found")
			return
		}
		writeError(w, http.StatusInternalServerError, "resolve failed")
		return
	}
	if !ok {
		writeError(w, http.StatusConflict, "review already resolved")
		return
	}

	pol, err := s.store.GetPolicy(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "policy fetch failed")
		return
	}

	frame := &pb.ServerFrame{
		Frame: &pb.ServerFrame_Verdict{Verdict: &pb.Verdict{
			EventId:   row.EventID,
			SessionId: row.SessionID,
			MadCode:   row.MADCode,
			Status:    reviewVerdictStatusProto(row.VerdictStatus),
			Policy:    s.policySnapshotProto(pol),
			Hitl:      &pb.HitlResponse{ContinueExecution: continueExec},
		}},
	}

	resp := reviewResolveResponse{Status: status, Resolved: true}
	if !s.publishHitlFrame(row.SessionID, frame) {
		slog.WarnContext(r.Context(), "reviews.publish_no_subscriber",
			"session_id", row.SessionID, "review_id", id)
		resp.Notice = "no SDK connected; resolution recorded but not pushed"
	}

	writeAuditLog(r.Context(), s.store, userID(r),
		"review_"+status, "hitl_queue",
		map[string]any{"id": id, "session_id": row.SessionID, "mad_code": row.MADCode})

	writeJSON(w, http.StatusOK, resp)
}

func reviewToSummary(r *store.HitlReview) reviewSummary {
	out := reviewSummary{
		ID:            r.ID,
		EventID:       r.EventID,
		VerdictID:     r.VerdictID,
		SessionID:     r.SessionID,
		MADCode:       r.MADCode,
		VerdictStatus: r.VerdictStatus,
		Status:        r.Status,
		CreatedAt:     r.CreatedAt.UTC().Format("2006-01-02T15:04:05.000Z"),
		ReviewedBy:    r.ReviewedBy,
	}
	if !r.ReviewedAt.IsZero() {
		out.ReviewedAt = r.ReviewedAt.UTC().Format("2006-01-02T15:04:05.000Z")
	}
	return out
}

func reviewVerdictStatusProto(status string) pb.VerdictStatus {
	switch status {
	case "error":
		return pb.VerdictStatus_VERDICT_STATUS_ERROR
	case "ok":
		return pb.VerdictStatus_VERDICT_STATUS_OK
	default:
		return pb.VerdictStatus_VERDICT_STATUS_UNSPECIFIED
	}
}
