// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api

import "net/http"

type overviewResponse struct {
	TotalEvents      int            `json:"total_events"`
	FlaggedVerdicts  int            `json:"flagged_verdicts"`
	ClassifierErrors int            `json:"classifier_errors"`
	PendingReviews   int            `json:"pending_reviews"`
	ActiveAgents     int            `json:"active_agents"`
	VerdictsByMAD    map[string]int `json:"verdicts_by_mad"`
	Window           string         `json:"window"`
}

type activityBucketEntry struct {
	Time  string `json:"time"`
	Count int    `json:"count"`
}

type activityResponse struct {
	Range   string                `json:"range"`
	Buckets []activityBucketEntry `json:"buckets"`
}

func (s *Server) handleStatsOverview(w http.ResponseWriter, r *http.Request) {
	o, err := s.store.StatsOverview(r.Context())
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	writeJSON(w, http.StatusOK, overviewResponse{
		TotalEvents:      o.TotalEvents,
		FlaggedVerdicts:  o.FlaggedVerdicts,
		ClassifierErrors: o.ClassifierErrors,
		PendingReviews:   o.PendingReviews,
		ActiveAgents:     o.ActiveAgents,
		VerdictsByMAD:    o.VerdictsByMAD,
		Window:           "24h",
	})
}

func (s *Server) handleStatsActivity(w http.ResponseWriter, r *http.Request) {
	rangeKey := r.URL.Query().Get("range")
	if rangeKey != "7d" {
		rangeKey = "24h"
	}
	buckets, err := s.store.StatsActivity(r.Context(), rangeKey)
	if err != nil {
		writeError(w, http.StatusInternalServerError, "query failed")
		return
	}
	resp := activityResponse{
		Range:   rangeKey,
		Buckets: make([]activityBucketEntry, 0, len(buckets)),
	}
	for _, b := range buckets {
		resp.Buckets = append(resp.Buckets, activityBucketEntry{
			Time:  b.Time.UTC().Format("2006-01-02T15:04:05Z"),
			Count: b.Count,
		})
	}
	writeJSON(w, http.StatusOK, resp)
}
