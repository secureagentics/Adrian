// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package store

import (
	"context"
	"time"
)

// Policy is the singleton row from the policies table.
type Policy struct {
	Mode                        string
	PolicyM0                    bool
	PolicyM2                    bool
	PolicyM3                    bool
	PolicyM4                    bool
	FailClosedOnClassifierError bool
	UpdatedAt                   time.Time
}

// PolicyPatch is the partial-update payload. Nil fields mean
// "no change".
type PolicyPatch struct {
	Mode                        *string
	PolicyM0                    *bool
	PolicyM2                    *bool
	PolicyM3                    *bool
	PolicyM4                    *bool
	FailClosedOnClassifierError *bool
}

// GetPolicy returns the singleton row. Migration 001 inserts a default
// row so this never returns ErrNotFound on a healthy database.
func (s *Store) GetPolicy(ctx context.Context) (*Policy, error) {
	row := s.db.QueryRowContext(ctx,
		`SELECT mode, policy_m0, policy_m2, policy_m3, policy_m4,
		        fail_closed_on_classifier_error, updated_at
		 FROM policies WHERE id = 1`)
	var p Policy
	var updatedAt string
	if err := row.Scan(&p.Mode, &p.PolicyM0, &p.PolicyM2, &p.PolicyM3, &p.PolicyM4,
		&p.FailClosedOnClassifierError, &updatedAt); err != nil {
		return nil, err
	}
	p.UpdatedAt = parseTime(updatedAt)
	return &p, nil
}

// UpdatePolicy applies a partial update to the singleton row. Nil
// fields are left unchanged via COALESCE.
func (s *Store) UpdatePolicy(ctx context.Context, patch *PolicyPatch) error {
	_, err := s.db.ExecContext(ctx,
		`UPDATE policies SET
		   mode      = COALESCE(?, mode),
		   policy_m0 = COALESCE(?, policy_m0),
		   policy_m2 = COALESCE(?, policy_m2),
		   policy_m3 = COALESCE(?, policy_m3),
		   policy_m4 = COALESCE(?, policy_m4),
		   fail_closed_on_classifier_error = COALESCE(?, fail_closed_on_classifier_error),
		   updated_at = (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
		 WHERE id = 1`,
		patch.Mode, boolPtrToInt(patch.PolicyM0), boolPtrToInt(patch.PolicyM2),
		boolPtrToInt(patch.PolicyM3), boolPtrToInt(patch.PolicyM4),
		boolPtrToInt(patch.FailClosedOnClassifierError))
	return err
}
