// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package store

import (
	"context"
	"database/sql"
	"errors"
	"time"

	"github.com/google/uuid"
)

// AgentRow is the read shape used by the runtime agents listing.
// AgentProfileID / AgentProfileName link the runtime agent (a LangGraph
// node name like "reason") to the operator-configured agent_profile
// it most recently produced events under. Empty strings when no event
// has been produced yet, or when the profile has been deleted.
type AgentRow struct {
	ID               string
	AgentID          string
	AgentProfileID   string
	AgentProfileName string
	FirstSeen        time.Time
	LastSeen         time.Time
	EventCount       int
	WorstMAD         string
}

// AgentSession describes one session an agent has produced events under.
type AgentSession struct {
	SessionID  string
	StartedAt  time.Time
	EndedAt    time.Time
	EventCount int
}

// UpsertAgent records that we've just seen an event from this agent.
// First sighting inserts; subsequent sightings refresh last_seen.
// Caller checks that agentID is non-empty.
func (s *Store) UpsertAgent(ctx context.Context, agentID string) error {
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO agents (id, agent_id, first_seen, last_seen)
		 VALUES (?, ?, ?, ?)
		 ON CONFLICT (agent_id) DO UPDATE SET last_seen = excluded.last_seen`,
		uuid.NewString(), agentID, now, now,
	)
	return err
}

// ListAgents returns a page of runtime agent rows (most recently seen
// first), each annotated with its event count and worst (highest
// severity) MAD code observed across its events. Severity ordering:
// M4 > M3 > M2 > everything else.
func (s *Store) ListAgents(ctx context.Context, perPage, offset int) ([]*AgentRow, int, error) {
	var total int
	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM agents`,
	).Scan(&total); err != nil {
		return nil, 0, err
	}

	rows, err := s.db.QueryContext(ctx,
		`SELECT
		     a.id, a.agent_id, a.first_seen, a.last_seen,
		     COALESCE(
		         (SELECT count(*) FROM events e WHERE e.agent_id = a.agent_id),
		         0
		     ) AS event_count,
		     COALESCE(
		         (SELECT v.mad_code FROM verdicts v
		          JOIN events e ON e.id = v.event_id
		          WHERE e.agent_id = a.agent_id
		          ORDER BY CASE
		              WHEN v.mad_code LIKE 'M4%' THEN 1
		              WHEN v.mad_code LIKE 'M3%' THEN 2
		              WHEN v.mad_code LIKE 'M2%' THEN 3
		              ELSE 4
		          END, v.created_at DESC
		          LIMIT 1),
		         ''
		     ) AS worst_mad,
		     COALESCE(
		         (SELECT e.agent_profile_id FROM events e
		          WHERE e.agent_id = a.agent_id AND e.agent_profile_id IS NOT NULL
		          ORDER BY e.created_at DESC
		          LIMIT 1),
		         ''
		     ) AS agent_profile_id,
		     COALESCE(
		         (SELECT ap.name FROM agent_profiles ap
		          WHERE ap.id = (
		              SELECT e.agent_profile_id FROM events e
		              WHERE e.agent_id = a.agent_id AND e.agent_profile_id IS NOT NULL
		              ORDER BY e.created_at DESC
		              LIMIT 1
		          )),
		         ''
		     ) AS agent_profile_name
		 FROM agents a
		 ORDER BY agent_profile_name = '' ASC, agent_profile_name ASC, a.last_seen DESC
		 LIMIT ? OFFSET ?`,
		perPage, offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()

	out := []*AgentRow{}
	for rows.Next() {
		r := &AgentRow{}
		var firstSeen, lastSeen string
		if err := rows.Scan(
			&r.ID, &r.AgentID, &firstSeen, &lastSeen,
			&r.EventCount, &r.WorstMAD,
			&r.AgentProfileID, &r.AgentProfileName,
		); err != nil {
			return nil, 0, err
		}
		r.FirstSeen = parseTime(firstSeen)
		r.LastSeen = parseTime(lastSeen)
		out = append(out, r)
	}
	return out, total, rows.Err()
}

// GetAgent looks up a single runtime agent plus the most recent
// sessions it has produced events under. Returns ErrNotFound if no
// row matches.
func (s *Store) GetAgent(ctx context.Context, agentID string) (*AgentRow, []*AgentSession, error) {
	r := &AgentRow{AgentID: agentID}
	var firstSeen, lastSeen string
	err := s.db.QueryRowContext(ctx,
		`SELECT
		     a.id, a.first_seen, a.last_seen,
		     COALESCE(
		         (SELECT count(*) FROM events e WHERE e.agent_id = a.agent_id),
		         0
		     ) AS event_count,
		     COALESCE(
		         (SELECT v.mad_code FROM verdicts v
		          JOIN events e ON e.id = v.event_id
		          WHERE e.agent_id = a.agent_id
		          ORDER BY CASE
		              WHEN v.mad_code LIKE 'M4%' THEN 1
		              WHEN v.mad_code LIKE 'M3%' THEN 2
		              WHEN v.mad_code LIKE 'M2%' THEN 3
		              ELSE 4
		          END, v.created_at DESC
		          LIMIT 1),
		         ''
		     ) AS worst_mad,
		     COALESCE(
		         (SELECT e.agent_profile_id FROM events e
		          WHERE e.agent_id = a.agent_id AND e.agent_profile_id IS NOT NULL
		          ORDER BY e.created_at DESC
		          LIMIT 1),
		         ''
		     ) AS agent_profile_id,
		     COALESCE(
		         (SELECT ap.name FROM agent_profiles ap
		          WHERE ap.id = (
		              SELECT e.agent_profile_id FROM events e
		              WHERE e.agent_id = a.agent_id AND e.agent_profile_id IS NOT NULL
		              ORDER BY e.created_at DESC
		              LIMIT 1
		          )),
		         ''
		     ) AS agent_profile_name
		 FROM agents a
		 WHERE a.agent_id = ?`,
		agentID,
	).Scan(
		&r.ID, &firstSeen, &lastSeen,
		&r.EventCount, &r.WorstMAD,
		&r.AgentProfileID, &r.AgentProfileName,
	)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, nil, ErrNotFound
		}
		return nil, nil, err
	}
	r.FirstSeen = parseTime(firstSeen)
	r.LastSeen = parseTime(lastSeen)

	sessRows, err := s.db.QueryContext(ctx,
		`SELECT session_id, min(created_at), max(created_at), count(*)
		 FROM events
		 WHERE agent_id = ?
		 GROUP BY session_id
		 ORDER BY max(created_at) DESC
		 LIMIT 20`,
		agentID)
	if err != nil {
		return nil, nil, err
	}
	defer sessRows.Close()

	var sessions []*AgentSession
	for sessRows.Next() {
		s := &AgentSession{}
		var startedAt, endedAt string
		if err := sessRows.Scan(&s.SessionID, &startedAt, &endedAt, &s.EventCount); err != nil {
			return nil, nil, err
		}
		s.StartedAt = parseTime(startedAt)
		s.EndedAt = parseTime(endedAt)
		sessions = append(sessions, s)
	}
	return r, sessions, sessRows.Err()
}
