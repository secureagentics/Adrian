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

// Event is one row to insert into the events table.
type Event struct {
	ID             string
	SessionID      string
	AgentID        string
	AgentProfileID *string
	EventType      string
	RunID          string
	PayloadJSON    string
	TokensUsed     int32
}

// EventListRow is the read shape (with agent_name joined and the latest
// verdict when one exists).
type EventListRow struct {
	ID             string
	SessionID      string
	AgentID        string
	AgentProfileID *string
	AgentName      string
	EventType      string
	RunID          string
	PayloadJSON    string
	TokensUsed     int32
	CreatedAt      time.Time
	VerdictID      string
	MADCode        string
	Classification string
}

// TimelineRow is one entry in a session timeline: an event with its
// latest verdict optionally attached.
type TimelineRow struct {
	ID             string
	EventType      string
	RunID          string
	AgentID        string
	AgentName      string
	PayloadJSON    string
	CreatedAt      time.Time
	VerdictID      string
	MADCode        string
	Classification string
}

// EventFilters is the query-string surface for ListEvents.
type EventFilters struct {
	Since     time.Time // events with created_at >= Since
	AgentID   string    // exact match (empty = no filter)
	SessionID string
	EventType string
	// MinMAD: when non-empty, restrict to events whose latest verdict's
	// mad_code starts with a tier >= this. Accepts "M2", "M3", "M4".
	// Lets the dashboard surface flagged events that didn't trigger a
	// HITL hold (post-execution tool pairs, tool_call-less LLM pairs).
	MinMAD string
}

// InsertEvent persists one paired event and reports whether a new row
// was inserted. The payload column holds the full JSON-encoded
// PairedEvent blob; downstream readers (dashboard, engine) re-decode it
// as needed. SDK retries can replay the same event_id.
func (s *Store) InsertEvent(ctx context.Context, e *Event) (bool, error) {
	res, err := s.db.ExecContext(ctx,
		`INSERT INTO events
		   (id, session_id, agent_id, agent_profile_id, event_type, run_id, payload, tokens_used)
		 VALUES (?, ?, ?, ?, ?, ?, ?, ?)
		 ON CONFLICT (id) DO NOTHING`,
		e.ID, e.SessionID, e.AgentID, e.AgentProfileID, e.EventType, e.RunID, e.PayloadJSON, e.TokensUsed)
	if err != nil {
		return false, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return false, err
	}
	return n > 0, nil
}

// ListEvents returns a page of events matching the filters, plus the
// total count for pagination. LEFT JOINs agent_profiles so each row
// carries the customer-facing agent name.
func (s *Store) ListEvents(ctx context.Context, f EventFilters, perPage, offset int) ([]*EventListRow, int, error) {
	where, args := eventsWhere(f)

	var total int
	if err := s.db.QueryRowContext(ctx,
		"SELECT count(*) FROM events e WHERE "+where, args...,
	).Scan(&total); err != nil {
		return nil, 0, err
	}

	args = append(args, perPage, offset)
	rows, err := s.db.QueryContext(ctx,
		`SELECT e.id, e.session_id, COALESCE(e.agent_id, ''), e.agent_profile_id,
		        COALESCE(ap.name, ''), e.event_type, COALESCE(e.run_id, ''),
		        e.created_at,
		        COALESCE(v.id, ''), COALESCE(v.mad_code, ''), COALESCE(v.classification, '')
		 FROM events e
		 LEFT JOIN agent_profiles ap ON ap.id = e.agent_profile_id
		 LEFT JOIN verdicts v ON v.event_id = e.id
		     AND v.created_at = (
		         SELECT max(v2.created_at) FROM verdicts v2 WHERE v2.event_id = e.id
		     )
		 WHERE `+where+`
		 ORDER BY e.created_at DESC
		 LIMIT ? OFFSET ?`, args...)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	out := []*EventListRow{}
	for rows.Next() {
		r, err := scanEventListRow(rows)
		if err != nil {
			return nil, 0, err
		}
		out = append(out, r)
	}
	return out, total, rows.Err()
}

// GetEvent returns one event by id (with agent_name joined), or
// ErrNotFound.
func (s *Store) GetEvent(ctx context.Context, id string) (*EventListRow, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT e.id, e.session_id, COALESCE(e.agent_id, ''), e.agent_profile_id,
		        COALESCE(ap.name, ''), e.event_type, COALESCE(e.run_id, ''),
		        e.payload, e.tokens_used, e.created_at
		 FROM events e
		 LEFT JOIN agent_profiles ap ON ap.id = e.agent_profile_id
		 WHERE e.id = ?`, id)
	r := &EventListRow{}
	var agentProfileID sql.NullString
	var createdAt string
	if err := row.Scan(&r.ID, &r.SessionID, &r.AgentID, &agentProfileID, &r.AgentName,
		&r.EventType, &r.RunID, &r.PayloadJSON, &r.TokensUsed, &createdAt); err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if agentProfileID.Valid {
		r.AgentProfileID = &agentProfileID.String
	}
	r.CreatedAt = parseTime(createdAt)
	return r, nil
}

func scanEventListRow(rows *sql.Rows) (*EventListRow, error) {
	r := &EventListRow{}
	var agentProfileID sql.NullString
	var createdAt string
	if err := rows.Scan(&r.ID, &r.SessionID, &r.AgentID, &agentProfileID, &r.AgentName,
		&r.EventType, &r.RunID, &createdAt,
		&r.VerdictID, &r.MADCode, &r.Classification); err != nil {
		return nil, err
	}
	if agentProfileID.Valid {
		r.AgentProfileID = &agentProfileID.String
	}
	r.CreatedAt = parseTime(createdAt)
	return r, nil
}

// SessionTimeline returns every event for a session in chronological
// order, each one annotated with its latest verdict (if any). The join
// picks the newest verdict per event_id so a re-classification doesn't
// surface stale rows.
func (s *Store) SessionTimeline(ctx context.Context, sessionID string) ([]*TimelineRow, error) {
	rows, err := s.db.QueryContext(ctx,
		`SELECT
		     e.id,
		     e.event_type,
		     COALESCE(e.run_id, ''),
		     COALESCE(e.agent_id, ''),
		     COALESCE(ap.name, ''),
		     e.payload,
		     e.created_at,
		     COALESCE(v.id, ''),
		     COALESCE(v.mad_code, ''),
		     COALESCE(v.classification, '')
		 FROM events e
		 LEFT JOIN agent_profiles ap ON ap.id = e.agent_profile_id
		 LEFT JOIN verdicts v ON v.event_id = e.id
		     AND v.created_at = (
		         SELECT max(v2.created_at) FROM verdicts v2 WHERE v2.event_id = e.id
		     )
		 WHERE e.session_id = ?
		 ORDER BY e.created_at ASC`,
		sessionID)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []*TimelineRow{}
	for rows.Next() {
		r := &TimelineRow{}
		var createdAt string
		if err := rows.Scan(
			&r.ID, &r.EventType, &r.RunID, &r.AgentID, &r.AgentName,
			&r.PayloadJSON, &createdAt,
			&r.VerdictID, &r.MADCode, &r.Classification,
		); err != nil {
			return nil, err
		}
		r.CreatedAt = parseTime(createdAt)
		out = append(out, r)
	}
	return out, rows.Err()
}

func eventsWhere(f EventFilters) (string, []any) {
	parts := []string{"e.created_at >= ?"}
	args := []any{f.Since.UTC().Format(time.RFC3339Nano)}
	if f.AgentID != "" {
		parts = append(parts, "e.agent_id = ?")
		args = append(args, f.AgentID)
	}
	if f.SessionID != "" {
		parts = append(parts, "e.session_id = ?")
		args = append(args, f.SessionID)
	}
	if f.EventType != "" {
		parts = append(parts, "e.event_type = ?")
		args = append(args, f.EventType)
	}
	if tiers := tiersAtOrAbove(f.MinMAD); len(tiers) > 0 {
		// Match the latest verdict per event so the filter stays in
		// sync with what GetVerdictByEventID / SessionTimeline return
		// to the dashboard. Without the MAX(created_at) constraint a
		// re-classify with an older M3 verdict would surface an event
		// whose displayed verdict is M0.
		placeholders := strings.TrimSuffix(strings.Repeat("?,", len(tiers)), ",")
		parts = append(parts, "EXISTS (SELECT 1 FROM verdicts v "+
			"WHERE v.event_id = e.id "+
			"AND v.created_at = (SELECT max(v2.created_at) FROM verdicts v2 WHERE v2.event_id = e.id) "+
			"AND substr(v.mad_code, 1, 2) IN ("+placeholders+"))")
		for _, t := range tiers {
			args = append(args, t)
		}
	}
	return strings.Join(parts, " AND "), args
}

// tiersAtOrAbove returns the M-tier prefixes >= floor. Floor of "M3"
// returns ["M3","M4"]; "M2" returns ["M2","M3","M4"]; anything else
// returns nil (no filter applied). M0 is intentionally excluded, it's
// the benign default and "M0+" is the same as no filter.
func tiersAtOrAbove(floor string) []string {
	switch floor {
	case "M2":
		return []string{"M2", "M3", "M4"}
	case "M3":
		return []string{"M3", "M4"}
	case "M4":
		return []string{"M4"}
	}
	return nil
}
