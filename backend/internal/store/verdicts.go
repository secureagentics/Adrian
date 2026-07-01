// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package store

import (
	"context"
	"database/sql"
	"errors"
	"strings"
	"time"
)

// Verdict is one row to insert into the verdicts table.
type Verdict struct {
	ID             string
	EventID        string
	SessionID      string
	AgentProfileID *string
	MADCode        string
	Classification string
	VerdictStatus  string
	Reasoning      *string
	LatencyMS      *int64
	TokensUsed     int32
}

// VerdictListRow is the read shape for the dashboard verdict feed.
type VerdictListRow struct {
	ID             string
	EventID        string
	SessionID      string
	MADCode        string
	Classification string
	VerdictStatus  string
	Reasoning      string
	LatencyMS      *int64
	TokensUsed     int32
	CreatedAt      time.Time
}

// VerdictFilters is the query-string surface for ListVerdicts.
type VerdictFilters struct {
	Since          time.Time
	Classification string // exact match (empty = no filter)
	MADCode        string // exact match (empty = no filter)
	VerdictStatus  string // exact match (empty = no filter)
}

// InsertVerdict persists one classification result.
func (s *Store) InsertVerdict(ctx context.Context, v *Verdict) error {
	status := v.VerdictStatus
	if status == "" {
		status = "ok"
	}
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO verdicts
		   (id, event_id, session_id, agent_profile_id, mad_code, classification, verdict_status, reasoning, latency_ms, tokens_used)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)`,
		v.ID, v.EventID, v.SessionID, v.AgentProfileID,
		v.MADCode, v.Classification, status, v.Reasoning, v.LatencyMS, v.TokensUsed)
	return err
}

// ListVerdicts returns a page of verdicts matching the filters, plus
// the total count for pagination.
func (s *Store) ListVerdicts(ctx context.Context, f VerdictFilters, perPage, offset int) ([]*VerdictListRow, int, error) {
	where, args := verdictsWhere(f)

	var total int
	if err := s.db.QueryRowContext(ctx,
		"SELECT count(*) FROM verdicts WHERE "+where, args...,
	).Scan(&total); err != nil {
		return nil, 0, err
	}

	args = append(args, perPage, offset)
	rows, err := s.db.QueryContext(ctx,
		`SELECT id, event_id, session_id, mad_code, classification, verdict_status,
		        COALESCE(reasoning, ''), latency_ms, tokens_used, created_at
		 FROM verdicts
		 WHERE `+where+`
		 ORDER BY created_at DESC
		 LIMIT ? OFFSET ?`, args...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	out := []*VerdictListRow{}
	for rows.Next() {
		r := &VerdictListRow{}
		var latency sql.NullInt64
		var createdAt string
		if err := rows.Scan(&r.ID, &r.EventID, &r.SessionID, &r.MADCode, &r.Classification, &r.VerdictStatus,
			&r.Reasoning, &latency, &r.TokensUsed, &createdAt); err != nil {
			return nil, 0, err
		}
		if latency.Valid {
			r.LatencyMS = &latency.Int64
		}
		r.CreatedAt = parseTime(createdAt)
		out = append(out, r)
	}
	return out, total, rows.Err()
}

// GetVerdictByEventID returns the verdict associated with one event,
// or ErrNotFound.
func (s *Store) GetVerdictByEventID(ctx context.Context, eventID string) (*VerdictListRow, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT id, event_id, session_id, mad_code, classification, verdict_status,
		        COALESCE(reasoning, ''), latency_ms, tokens_used, created_at
		 FROM verdicts WHERE event_id = ?
		 ORDER BY created_at DESC LIMIT 1`, eventID)
	r := &VerdictListRow{}
	var latency sql.NullInt64
	var createdAt string
	if err := row.Scan(&r.ID, &r.EventID, &r.SessionID, &r.MADCode, &r.Classification, &r.VerdictStatus,
		&r.Reasoning, &latency, &r.TokensUsed, &createdAt); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if latency.Valid {
		r.LatencyMS = &latency.Int64
	}
	r.CreatedAt = parseTime(createdAt)
	return r, nil
}

func verdictsWhere(f VerdictFilters) (string, []any) {
	parts := []string{"created_at >= ?"}
	args := []any{f.Since.UTC().Format(time.RFC3339Nano)}
	if f.Classification != "" {
		parts = append(parts, "classification = ?")
		args = append(args, f.Classification)
	}
	if f.MADCode != "" {
		parts = append(parts, "mad_code = ?")
		args = append(args, f.MADCode)
	}
	if f.VerdictStatus != "" {
		parts = append(parts, "verdict_status = ?")
		args = append(args, f.VerdictStatus)
	}
	return strings.Join(parts, " AND "), args
}
