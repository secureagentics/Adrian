// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package store

import (
	"context"
	"time"
)

// Overview is the 24h summary the dashboard home renders.
type Overview struct {
	TotalEvents      int
	FlaggedVerdicts  int
	ClassifierErrors int
	PendingReviews   int
	ActiveAgents     int
	VerdictsByMAD    map[string]int
}

// ActivityBucket is one bin in the time-series response.
type ActivityBucket struct {
	Time  time.Time
	Count int
}

// StatsOverview returns a 24h overview snapshot derived from events,
// verdicts, hitl_queue, and agents (last_seen).
func (s *Store) StatsOverview(ctx context.Context) (*Overview, error) {
	const window = "-24 hours"
	o := &Overview{VerdictsByMAD: map[string]int{}}

	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM events WHERE created_at >= datetime('now', ?)`, window,
	).Scan(&o.TotalEvents); err != nil {
		return nil, err
	}

	// Flagged = real non-M0 MAD findings. Classifier errors are tracked
	// separately below so outages do not inflate security-finding totals.
	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM verdicts
		 WHERE created_at >= datetime('now', ?)
		   AND verdict_status = 'ok'
		   AND mad_code != '' AND mad_code NOT LIKE 'M0%'`, window,
	).Scan(&o.FlaggedVerdicts); err != nil {
		return nil, err
	}

	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM verdicts
		 WHERE created_at >= datetime('now', ?)
		   AND verdict_status = 'error'`, window,
	).Scan(&o.ClassifierErrors); err != nil {
		return nil, err
	}

	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM hitl_queue WHERE status = 'pending'`,
	).Scan(&o.PendingReviews); err != nil {
		return nil, err
	}

	if err := s.db.QueryRowContext(ctx,
		`SELECT count(*) FROM agents WHERE last_seen >= datetime('now', ?)`, window,
	).Scan(&o.ActiveAgents); err != nil {
		return nil, err
	}

	rows, err := s.db.QueryContext(ctx,
		`SELECT
		     CASE
		         WHEN verdict_status = 'error' THEN 'error'
		         WHEN mad_code LIKE 'M0%' THEN 'M0'
		         WHEN mad_code LIKE 'M2%' THEN 'M2'
		         WHEN mad_code LIKE 'M3%' THEN 'M3'
		         WHEN mad_code LIKE 'M4%' THEN 'M4'
		         ELSE 'other'
		     END AS family,
		     count(*)
		 FROM verdicts
		 WHERE created_at >= datetime('now', ?)
		 GROUP BY family`, window)
	if err != nil {
		return nil, err
	}
	defer rows.Close()
	for rows.Next() {
		var family string
		var count int
		if err := rows.Scan(&family, &count); err != nil {
			return nil, err
		}
		o.VerdictsByMAD[family] = count
	}
	return o, rows.Err()
}

// StatsActivity returns event counts bucketed by hour (range="24h") or
// by day (range="7d"). Missing buckets are not emitted; the caller
// fills zeros if a contiguous series is wanted.
func (s *Store) StatsActivity(ctx context.Context, rangeKey string) ([]ActivityBucket, error) {
	var bucketFmt, since string
	switch rangeKey {
	case "7d":
		bucketFmt = "%Y-%m-%dT00:00:00Z"
		since = "-7 days"
	default:
		// "24h" is the default.
		bucketFmt = "%Y-%m-%dT%H:00:00Z"
		since = "-24 hours"
	}

	rows, err := s.db.QueryContext(ctx,
		`SELECT strftime(?, created_at) AS bucket, count(*)
		 FROM events
		 WHERE created_at >= datetime('now', ?)
		 GROUP BY bucket
		 ORDER BY bucket ASC`,
		bucketFmt, since)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []ActivityBucket{}
	for rows.Next() {
		var bucketStr string
		var count int
		if err := rows.Scan(&bucketStr, &count); err != nil {
			return nil, err
		}
		t, err := time.Parse("2006-01-02T15:04:05Z", bucketStr)
		if err != nil {
			continue
		}
		out = append(out, ActivityBucket{Time: t, Count: count})
	}
	return out, rows.Err()
}
