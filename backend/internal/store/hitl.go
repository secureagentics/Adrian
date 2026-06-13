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

// HitlReview is a row from hitl_queue, plus joined fields the dashboard
// list view needs.
type HitlReview struct {
	ID            string
	EventID       string
	VerdictID     string
	SessionID     string
	MADCode       string
	VerdictStatus string
	Status        string
	ReviewedBy    string
	ReviewedAt    time.Time
	CreatedAt     time.Time
}

// HitlReviewDetail extends HitlReview with the event payload + verdict
// reasoning so the dashboard's review pane can render in full.
type HitlReviewDetail struct {
	HitlReview
	EventPayloadJSON string
	Classification   string
	Reasoning        string
}

// InsertHitlQueue records a pending review row. The hitl_queue UNIQUE
// constraint on event_id de-dups if a duplicate verdict races; we use
// INSERT OR IGNORE rather than panicking on the second insert.
func (s *Store) InsertHitlQueue(ctx context.Context, eventID, verdictID, sessionID, madCode string) error {
	_, err := s.db.ExecContext(ctx,
		`INSERT OR IGNORE INTO hitl_queue (id, event_id, verdict_id, session_id, mad_code)
		 VALUES (?, ?, ?, ?, ?)`,
		uuid.NewString(), eventID, verdictID, sessionID, madCode)
	return err
}

// ListHitlQueue returns rows in the requested status (default 'pending'),
// newest first, paginated.
func (s *Store) ListHitlQueue(ctx context.Context, status string, perPage, offset int) ([]*HitlReview, int, error) {
	if status == "" {
		status = "pending"
	}
	var total int
	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM hitl_queue WHERE status = ?`, status,
	).Scan(&total); err != nil {
		return nil, 0, err
	}
	rows, err := s.db.QueryContext(ctx,
		`SELECT q.id, q.event_id, COALESCE(q.verdict_id, ''), COALESCE(q.session_id, ''),
		        q.mad_code, COALESCE(v.verdict_status, 'ok'), q.status, COALESCE(q.reviewed_by, ''),
		        COALESCE(q.reviewed_at, ''), q.created_at
		 FROM hitl_queue q
		 LEFT JOIN verdicts v ON v.id = q.verdict_id
		 WHERE q.status = ?
		 ORDER BY q.created_at DESC
		 LIMIT ? OFFSET ?`,
		status, perPage, offset)
	if err != nil {
		return nil, 0, err
	}
	defer rows.Close()
	out := []*HitlReview{}
	for rows.Next() {
		r := &HitlReview{}
		var reviewedAt, createdAt string
		if err := rows.Scan(&r.ID, &r.EventID, &r.VerdictID, &r.SessionID,
			&r.MADCode, &r.VerdictStatus, &r.Status, &r.ReviewedBy, &reviewedAt, &createdAt); err != nil {
			return nil, 0, err
		}
		if reviewedAt != "" {
			r.ReviewedAt = parseTime(reviewedAt)
		}
		r.CreatedAt = parseTime(createdAt)
		out = append(out, r)
	}
	return out, total, rows.Err()
}

// GetHitlReview returns one queue row with the event payload + verdict
// reasoning joined. Returns ErrNotFound if no row matches the id.
func (s *Store) GetHitlReview(ctx context.Context, id string) (*HitlReviewDetail, error) {
	r := &HitlReviewDetail{}
	var reviewedAt, createdAt string
	var classification, reasoning sql.NullString
	err := s.db.QueryRowContext(ctx,
		`SELECT
		     q.id, q.event_id, COALESCE(q.verdict_id, ''), COALESCE(q.session_id, ''),
		     q.mad_code, q.status, COALESCE(q.reviewed_by, ''),
		     COALESCE(q.reviewed_at, ''), q.created_at,
		     COALESCE(e.payload, ''),
		     v.classification, COALESCE(v.verdict_status, 'ok'), v.reasoning
		 FROM hitl_queue q
		 LEFT JOIN events e   ON e.id = q.event_id
		 LEFT JOIN verdicts v ON v.id = q.verdict_id
		 WHERE q.id = ?`, id,
	).Scan(
		&r.ID, &r.EventID, &r.VerdictID, &r.SessionID,
		&r.MADCode, &r.Status, &r.ReviewedBy,
		&reviewedAt, &createdAt,
		&r.EventPayloadJSON,
		&classification, &r.VerdictStatus, &reasoning,
	)
	if err != nil {
		if errors.Is(err, sql.ErrNoRows) {
			return nil, ErrNotFound
		}
		return nil, err
	}
	if reviewedAt != "" {
		r.ReviewedAt = parseTime(reviewedAt)
	}
	r.CreatedAt = parseTime(createdAt)
	if classification.Valid {
		r.Classification = classification.String
	}
	if reasoning.Valid {
		r.Reasoning = reasoning.String
	}
	return r, nil
}

// ResolveHitl atomically transitions a pending row to status. Returns
// the joined detail (so the caller can build the resolution Verdict
// frame) and ok=false when the row was already resolved by a concurrent
// click. Returns ErrNotFound when the id doesn't exist.
func (s *Store) ResolveHitl(ctx context.Context, id, status, reviewerUserID string) (*HitlReviewDetail, bool, error) {
	row, err := s.GetHitlReview(ctx, id)
	if err != nil {
		return nil, false, err
	}
	if row.Status != "pending" {
		return row, false, nil
	}
	now := time.Now().UTC().Format("2006-01-02T15:04:05.000Z")
	var uid sql.NullString
	if reviewerUserID != "" {
		uid = sql.NullString{String: reviewerUserID, Valid: true}
	}
	res, err := s.db.ExecContext(ctx,
		`UPDATE hitl_queue
		 SET status = ?, reviewed_by = ?, reviewed_at = ?
		 WHERE id = ? AND status = 'pending'`,
		status, uid, now, id)
	if err != nil {
		return nil, false, err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return nil, false, err
	}
	if n == 0 {
		// Lost the race with another reviewer; refresh and report.
		fresh, _ := s.GetHitlReview(ctx, id)
		return fresh, false, nil
	}
	row.Status = status
	row.ReviewedBy = reviewerUserID
	row.ReviewedAt = parseTime(now)
	return row, true, nil
}
