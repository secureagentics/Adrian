// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package notifications

import (
	"context"
	"log/slog"
	"strings"
	"time"

	"github.com/secureagentics/Adrian/backend/internal/store"
)

// VerdictNotification is the input the dispatcher consumes per event.
// The WS handler builds it from the engine.Verdict and the paired event.
type VerdictNotification struct {
	EventID        string
	SessionID      string
	AgentID        string
	MADCode        string
	Classification string
}

// Dispatcher fans verdict notifications out to user-configured Discord
// webhooks. One goroutine consumes the verdicts channel; per-webhook
// HTTP POSTs run inline (Discord typically responds in <1s; if a webhook
// is slow it briefly stalls subsequent fanouts but doesn't block the WS
// handler since the channel is buffered and the WS-side send uses a
// non-blocking select).
type Dispatcher struct {
	store        *store.Store
	verdicts     chan VerdictNotification
	dashboardURL string
}

// NewDispatcher wires the dispatcher. dashboardURL is the public base
// URL the deep link in each Discord message points at.
func NewDispatcher(st *store.Store, dashboardURL string) *Dispatcher {
	return &Dispatcher{
		store:        st,
		verdicts:     make(chan VerdictNotification, 64),
		dashboardURL: strings.TrimRight(dashboardURL, "/"),
	}
}

// Verdicts returns the input channel the WS handler writes to.
// Sends should use a non-blocking select to avoid stalling classify.
func (d *Dispatcher) Verdicts() chan<- VerdictNotification {
	return d.verdicts
}

// Run consumes notifications until ctx is cancelled. Safe to invoke as
// `go dispatcher.Run(ctx)` from main.
func (d *Dispatcher) Run(ctx context.Context) {
	for {
		select {
		case <-ctx.Done():
			return
		case vn := <-d.verdicts:
			d.fanout(ctx, vn)
		}
	}
}

// fanout looks up enabled webhooks matching this verdict's MAD family
// and sends to each. Logs warn on individual send failures; does not
// retry (Discord is idempotent for the user, and a queued retry layer
// would mean state outside SQLite).
func (d *Dispatcher) fanout(ctx context.Context, vn VerdictNotification) {
	if vn.MADCode == "" || strings.HasPrefix(vn.MADCode, "M0") {
		// Empty MAD codes (classifier errors) and M0 benign verdicts do
		// not fan out; these webhooks are for real flagged MAD findings.
		// Operational outage alerts should be a separate alert type.
		return
	}
	hooks, err := d.store.ListWebhooks(ctx, true)
	if err != nil {
		slog.WarnContext(ctx, "notifications.list_failed", "error", err)
		return
	}
	if len(hooks) == 0 {
		return
	}
	alert := Alert{
		EventID:        vn.EventID,
		SessionID:      vn.SessionID,
		AgentID:        vn.AgentID,
		MADCode:        vn.MADCode,
		Classification: vn.Classification,
		DashboardURL:   d.dashboardURL,
	}
	for _, h := range hooks {
		if !alertTypeMatches(h.AlertType, vn.MADCode) {
			continue
		}
		sendCtx, cancel := context.WithTimeout(ctx, 5*time.Second)
		if err := Send(sendCtx, h.WebhookURL, alert); err != nil {
			slog.WarnContext(ctx, "notifications.send_failed",
				"webhook_id", h.ID, "error", err, "event_id", vn.EventID)
		}
		cancel()
	}
}

// alertTypeMatches returns true when the verdict's MAD code is in the
// configured filter. 'all' matches every flagged code; 'M3' / 'M4'
// match only that family (prefix). M0 / M2 are filtered out upstream
// in fanout(); we accept them here for completeness.
func alertTypeMatches(filter, madCode string) bool {
	if filter == "all" {
		return true
	}
	return strings.HasPrefix(madCode, filter)
}
