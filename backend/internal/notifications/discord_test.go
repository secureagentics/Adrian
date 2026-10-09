// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package notifications

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"
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

// TestBuildPayloadUsesCuratedTextForM2fAndM2g guards the two M2 subcodes
// that the judge prompt defines. They once had no entry in alerts.json,
// so the embed fell back to the generic "Adrian flagged an event" text.
func TestBuildPayloadUsesCuratedTextForM2fAndM2g(t *testing.T) {
	cases := map[string]string{
		"M2.f": "Dispatch without constraint check",
		"M2.g": "Accepting physically implausible data",
	}
	for code, subcategory := range cases {
		p := buildPayload(Alert{
			SessionID:      "sess-1",
			MADCode:        code,
			Classification: "notify",
			DashboardURL:   "http://localhost:3000",
		})
		embed := p.Embeds[0]
		if !strings.Contains(embed.Title, subcategory) {
			t.Errorf("%s: title %q does not contain %q", code, embed.Title, subcategory)
		}
		if strings.Contains(embed.Description, "Open the dashboard for full context") {
			t.Errorf("%s: fell back to the generic description: %q", code, embed.Description)
		}
		var hasAction bool
		for _, f := range embed.Fields {
			if f.Name == "Action" && f.Value == "NOTIFY" {
				hasAction = true
			}
		}
		if !hasAction {
			t.Errorf("%s: embed is missing the Action=NOTIFY field", code)
		}
	}
}
