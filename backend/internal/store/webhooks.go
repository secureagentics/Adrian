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

// Webhook is one row from the webhooks table.
type Webhook struct {
	ID                string
	Platform          string
	WebhookURL        string
	AlertType         string // 'M3' | 'M4' | 'all'
	Enabled           bool
	InstalledByUserID string
	CreatedAt         time.Time
	UpdatedAt         time.Time
}

// CreateWebhook inserts a row. Caller has already validated the URL.
func (s *Store) CreateWebhook(ctx context.Context, id, webhookURL, alertType, userID string) error {
	var uid sql.NullString
	if userID != "" {
		uid = sql.NullString{String: userID, Valid: true}
	}
	_, err := s.db.ExecContext(ctx,
		`INSERT INTO webhooks (id, platform, webhook_url, alert_type, enabled, installed_by_user_id)
		 VALUES (?, 'discord', ?, ?, 1, ?)`,
		id, webhookURL, alertType, uid)
	return err
}

// ListWebhooks returns rows; pass enabledOnly=true to filter to active.
func (s *Store) ListWebhooks(ctx context.Context, enabledOnly bool) ([]*Webhook, error) {
	q := `SELECT id, platform, webhook_url, alert_type, enabled,
	             COALESCE(installed_by_user_id, ''), created_at, updated_at
	      FROM webhooks`
	if enabledOnly {
		q += ` WHERE enabled = 1`
	}
	q += ` ORDER BY created_at DESC`
	rows, err := s.db.QueryContext(ctx, q)
	if err != nil {
		return nil, err
	}
	defer rows.Close()

	out := []*Webhook{}
	for rows.Next() {
		w := &Webhook{}
		var enabled int
		var createdAt, updatedAt string
		if err := rows.Scan(&w.ID, &w.Platform, &w.WebhookURL, &w.AlertType, &enabled,
			&w.InstalledByUserID, &createdAt, &updatedAt); err != nil {
			return nil, err
		}
		w.Enabled = enabled == 1
		w.CreatedAt = parseTime(createdAt)
		w.UpdatedAt = parseTime(updatedAt)
		out = append(out, w)
	}
	return out, rows.Err()
}

// DeleteWebhook removes a row. Returns ErrNotFound if no row matched.
func (s *Store) DeleteWebhook(ctx context.Context, id string) error {
	res, err := s.db.ExecContext(ctx, `DELETE FROM webhooks WHERE id = ?`, id)
	if err != nil {
		return err
	}
	n, err := res.RowsAffected()
	if err != nil {
		return err
	}
	if n == 0 {
		return ErrNotFound
	}
	return nil
}

// MaskedURL returns the webhook URL with the secret token replaced by
// a fixed prefix + last 8 chars. Used in list responses so the UI never
// re-shows the full URL after the paste-once flow.
func MaskedURL(u string) string {
	const visibleSuffix = 8
	lastSlash := strings.LastIndex(u, "/")
	if lastSlash == -1 || lastSlash == len(u)-1 {
		return u
	}
	token := u[lastSlash+1:]
	if len(token) > visibleSuffix {
		token = token[len(token)-visibleSuffix:]
	}
	// Keep the host + the leading webhook path, mask only the token.
	// Discord shape: https://discord.com/api/webhooks/<id>/<token>
	return u[:lastSlash+1] + "...***" + token
}

// ErrInvalidWebhookURL is returned by the handler when the user-supplied
// URL doesn't match a known webhook host. Centralised so the dispatcher
// can reuse the validation.
var ErrInvalidWebhookURL = errors.New("invalid webhook URL")
