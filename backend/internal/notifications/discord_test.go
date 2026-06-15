// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package notifications

import (
	"context"
	"database/sql"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/secureagentics/Adrian/backend/internal/store"
	_ "modernc.org/sqlite"
)

func TestValidateDiscordWebhookURL(t *testing.T) {
	cases := []struct {
		in string
		ok bool
	}{
		{"https://discord.com/api/webhooks/1/abc", true},
		{"https://discordapp.com/api/webhooks/1/abc", true},
		{"http://discord.com/api/webhooks/1/abc", false},
		{"https://example.com/webhook", false},
		{"", false},
	}
	for _, c := range cases {
		err := ValidateDiscordWebhookURL(c.in)
		if (err == nil) != c.ok {
			t.Errorf("Validate(%q): err=%v, want ok=%v", c.in, err, c.ok)
		}
	}
}

// TestSendBuildsExpectedPayload runs Send against an httptest.Server
// that pretends to be Discord. The test inspects the body to confirm
// the payload shape (content + embed) and the deep-link target.
func TestSendBuildsExpectedPayload(t *testing.T) {
	var captured map[string]any

	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		_ = json.Unmarshal(body, &captured)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer mock.Close()

	// Pretend the mock is discord.com so ValidateDiscordWebhookURL
	// passes. The real validator checks the prefix; we override allowedHosts
	// for this test only.
	origHosts := allowedHosts
	allowedHosts = []string{mock.URL + "/"}
	defer func() { allowedHosts = origHosts }()

	alert := Alert{
		EventID:        "ev-1",
		SessionID:      "sess-1",
		AgentID:        "agent-x",
		MADCode:        "M3.b",
		Classification: "notify",
		DashboardURL:   "https://dash.example",
	}
	if err := Send(context.Background(), mock.URL+"/api/webhooks/1/tok", alert); err != nil {
		t.Fatalf("Send: %v", err)
	}

	if captured == nil {
		t.Fatal("mock received no body")
	}
	embeds, ok := captured["embeds"].([]any)
	if !ok || len(embeds) != 1 {
		t.Fatalf("expected 1 embed, got %v", captured["embeds"])
	}
	embed := embeds[0].(map[string]any)
	link := embed["url"].(string)
	want := "https://dash.example/sessions/sess-1#event-ev-1"
	if link != want {
		t.Errorf("embed.url = %q, want %q", link, want)
	}
	desc := embed["description"].(string)
	if strings.Contains(desc, "<reasoning>") {
		t.Errorf("embed.description must not leak reasoning tags, got %q", desc)
	}
	if desc == "" {
		t.Errorf("embed.description should not be empty")
	}
	// M3.b is in the curated bundle, the description should be the
	// canned text and the title should carry the severity label.
	if !strings.Contains(desc, "Your agent") {
		t.Errorf("expected canned description for M3.b, got %q", desc)
	}
	if title := embed["title"].(string); !strings.Contains(title, "High-Risk Misuse") {
		t.Errorf("title should carry the severity label, got %q", title)
	}
	// Top-level content stays focused on the alert summary; the link
	// lives on the embed only.
	if strings.Contains(captured["content"].(string), want) {
		t.Errorf("content should not duplicate the deep link, got %q", captured["content"])
	}
	// Embed field renders the link as a markdown hyperlink so the
	// label "Open in dashboard" is what the user clicks.
	fields := embed["fields"].([]any)
	var found bool
	for _, f := range fields {
		fm := f.(map[string]any)
		if fm["name"] == "Dashboard" && fm["value"] == "[Open in dashboard]("+want+")" {
			found = true
		}
	}
	if !found {
		t.Errorf("expected a Dashboard field carrying [Open in dashboard](url) markdown")
	}
}

func TestSendNonDiscordURLRejected(t *testing.T) {
	err := Send(context.Background(), "https://evil.example.com/x", Alert{})
	if err == nil {
		t.Fatal("expected validation error")
	}
}

func TestDispatcherSkipsEmptyMADCode(t *testing.T) {
	var posts int32
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		atomic.AddInt32(&posts, 1)
		w.WriteHeader(http.StatusNoContent)
	}))
	defer mock.Close()

	origHosts := allowedHosts
	allowedHosts = []string{mock.URL + "/"}
	defer func() { allowedHosts = origHosts }()

	db, err := sql.Open("sqlite", "file:notifications?mode=memory&cache=shared")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	defer db.Close()
	if _, err := db.Exec(`
CREATE TABLE webhooks (
    id                   TEXT PRIMARY KEY,
    platform             TEXT NOT NULL DEFAULT 'discord',
    webhook_url          TEXT NOT NULL,
    alert_type           TEXT NOT NULL,
    enabled              INTEGER NOT NULL DEFAULT 1,
    installed_by_user_id TEXT,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
`); err != nil {
		t.Fatalf("create webhooks: %v", err)
	}
	st := store.New(db)
	if err := st.CreateWebhook(context.Background(), uuid.NewString(), mock.URL+"/api/webhooks/1/tok", "all", ""); err != nil {
		t.Fatalf("create webhook: %v", err)
	}

	d := NewDispatcher(st, "https://dash.example")
	d.fanout(context.Background(), VerdictNotification{
		EventID:        "ev-error",
		SessionID:      "sess-error",
		MADCode:        "",
		Classification: "error",
	})
	if got := atomic.LoadInt32(&posts); got != 0 {
		t.Fatalf("webhook posts = %d, want 0 for empty MAD code", got)
	}
}

func TestSendRespectsContextDeadline(t *testing.T) {
	// Server that holds the response open longer than the client's
	// context allows. The handler exits when r.Context() is cancelled
	// (the client closes the connection on its own deadline) OR after a
	// short ceiling so the server's Close() never blocks the test.
	mock := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		select {
		case <-time.After(2 * time.Second):
			w.WriteHeader(http.StatusOK)
		case <-r.Context().Done():
			return
		}
	}))
	defer mock.Close()

	origHosts := allowedHosts
	allowedHosts = []string{mock.URL + "/"}
	defer func() { allowedHosts = origHosts }()

	ctx, cancel := context.WithTimeout(context.Background(), 100*time.Millisecond)
	defer cancel()
	err := Send(ctx, mock.URL+"/x", Alert{MADCode: "M3"})
	if err == nil {
		t.Fatal("expected timeout error")
	}
}
