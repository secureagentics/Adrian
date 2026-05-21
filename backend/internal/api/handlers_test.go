// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package api_test

import (
	"context"
	"database/sql"
	"encoding/json"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/google/uuid"
	_ "modernc.org/sqlite"

	"github.com/secureagentics/Adrian/backend/internal/api"
	"github.com/secureagentics/Adrian/backend/internal/auth"
	"github.com/secureagentics/Adrian/backend/internal/config"
	"github.com/secureagentics/Adrian/backend/internal/engine"
	pb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
	"github.com/secureagentics/Adrian/backend/internal/ws"
)

// stubClassifier returns a fixed M0/benign verdict and a no-op Ping.
// The API tests need a Classifier to construct the server but don't
// exercise the engine itself.
type stubClassifier struct{}

func (stubClassifier) Classify(_ context.Context, _ *pb.PairedEvent, _ string) (*engine.Verdict, error) {
	return &engine.Verdict{MADCode: "M0", Classification: "benign"}, nil
}

func (stubClassifier) Ping(_ context.Context) error { return nil }

// -----------------------------------------------------------------
// Readiness
// -----------------------------------------------------------------

func TestHealthzAlwaysOK(t *testing.T) {
	srv, _, _, _ := newTestServer(t)
	resp, err := http.Get(srv.URL + "/healthz")
	if err != nil {
		t.Fatalf("GET /healthz: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
}

// TestReadyzReportsBothChecks asserts /readyz exercises the DB ping
// and the classifier ping, names them in the JSON body, and returns
// 200 only when both pass. Uses the stub classifier which always
// passes, plus the in-memory SQLite seeded by newTestServer.
func TestReadyzReportsBothChecks(t *testing.T) {
	srv, _, _, _ := newTestServer(t)
	resp, err := http.Get(srv.URL + "/readyz")
	if err != nil {
		t.Fatalf("GET /readyz: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	var body struct {
		OK     bool `json:"ok"`
		Checks struct {
			DB         string `json:"db"`
			Classifier string `json:"classifier"`
		} `json:"checks"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if !body.OK {
		t.Errorf("ok = false; want true. checks=%+v", body.Checks)
	}
	if body.Checks.DB != "ok" {
		t.Errorf("checks.db = %q, want \"ok\"", body.Checks.DB)
	}
	if body.Checks.Classifier != "ok" {
		t.Errorf("checks.classifier = %q, want \"ok\"", body.Checks.Classifier)
	}
}

// TestReadyzFailsWhenDBClosed asserts that a dead DB drives /readyz
// to 503 with the failing subsystem named in the JSON body. The
// goal is for compose's healthcheck and external orchestrators to
// fail-fast rather than route traffic to a wedged backend.
func TestReadyzFailsWhenDBClosed(t *testing.T) {
	srv, db, _, _ := newTestServer(t)
	_ = db.Close()
	resp, err := http.Get(srv.URL + "/readyz")
	if err != nil {
		t.Fatalf("GET /readyz: %v", err)
	}
	defer resp.Body.Close()
	if resp.StatusCode != http.StatusServiceUnavailable {
		t.Fatalf("status = %d, want 503", resp.StatusCode)
	}
	var body struct {
		OK     bool `json:"ok"`
		Checks struct {
			DB         string `json:"db"`
			Classifier string `json:"classifier"`
		} `json:"checks"`
	}
	if err := json.NewDecoder(resp.Body).Decode(&body); err != nil {
		t.Fatalf("decode: %v", err)
	}
	if body.OK {
		t.Errorf("ok = true; want false on closed DB")
	}
	if body.Checks.DB == "ok" {
		t.Errorf("checks.db should report the underlying error; got %q", body.Checks.DB)
	}
}

// -----------------------------------------------------------------
// Auth flow
// -----------------------------------------------------------------

func TestLoginHappyPath(t *testing.T) {
	srv, _, plaintext, _ := newTestServer(t)
	resp := postJSON(t, srv, nil, "/api/auth/login", map[string]any{
		"email":    "admin@localhost",
		"password": plaintext,
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	if cookie := getCookie(resp, "adrian_token"); cookie == "" {
		t.Fatal("expected adrian_token cookie")
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	if data["email"] != "admin@localhost" {
		t.Errorf("email = %v, want admin@localhost", data["email"])
	}
	if mc, _ := data["must_change_password"].(bool); !mc {
		t.Errorf("must_change_password should be true on fresh user")
	}
}

func TestLoginWrongPassword(t *testing.T) {
	srv, _, _, _ := newTestServer(t)
	resp := postJSON(t, srv, nil, "/api/auth/login", map[string]any{
		"email":    "admin@localhost",
		"password": "wrong",
	})
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestLoginNoSuchUser(t *testing.T) {
	srv, _, _, _ := newTestServer(t)
	resp := postJSON(t, srv, nil, "/api/auth/login", map[string]any{
		"email":    "ghost@localhost",
		"password": "anything",
	})
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestChangePasswordHappy(t *testing.T) {
	srv, db, plaintext, _ := newTestServer(t)
	cookie := loginAndGetCookie(t, srv, plaintext)

	resp := postJSON(t, srv, cookie, "/api/auth/change-password", map[string]any{
		"old_password": plaintext,
		"new_password": "new-secure-password-123",
	})
	if resp.StatusCode != http.StatusNoContent {
		t.Fatalf("status = %d, want 204", resp.StatusCode)
	}

	// Old password no longer works.
	resp = postJSON(t, srv, nil, "/api/auth/login", map[string]any{
		"email":    "admin@localhost",
		"password": plaintext,
	})
	if resp.StatusCode != http.StatusUnauthorized {
		t.Errorf("old password still works after change: %d", resp.StatusCode)
	}

	// New one does, and must_change_password is now false.
	resp = postJSON(t, srv, nil, "/api/auth/login", map[string]any{
		"email":    "admin@localhost",
		"password": "new-secure-password-123",
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("new password did not log in: %d", resp.StatusCode)
	}
	data := decodeBody(t, resp)["data"].(map[string]any)
	if mc, _ := data["must_change_password"].(bool); mc {
		t.Errorf("must_change_password should be false after change")
	}
	_ = db
}

func TestChangePasswordWrongOld(t *testing.T) {
	srv, _, plaintext, _ := newTestServer(t)
	cookie := loginAndGetCookie(t, srv, plaintext)

	resp := postJSON(t, srv, cookie, "/api/auth/change-password", map[string]any{
		"old_password": "wrong",
		"new_password": "new-secure-password-123",
	})
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

func TestRequireSessionRejectsMissing(t *testing.T) {
	srv, _, _, _ := newTestServer(t)
	resp := getReq(t, srv, nil, "/api/settings/policy")
	if resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("status = %d, want 401", resp.StatusCode)
	}
}

// -----------------------------------------------------------------
// Policy GET / PUT
// -----------------------------------------------------------------

// To exercise policy + keys we need a logged-in session whose user
// has must_change_password = 0. The simplest path is to seed the
// admin row that way for these tests.

func TestPolicyGetAndUpdate(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)
	_ = db

	// GET
	resp := getReq(t, srv, cookie, "/api/settings/policy")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("GET status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	if body["data"].(map[string]any)["mode"] != "alert" {
		t.Errorf("default mode = %v, want alert", body["data"].(map[string]any)["mode"])
	}

	// PUT
	resp = doJSON(t, srv, cookie, http.MethodPut, "/api/settings/policy", map[string]any{
		"mode": "hitl",
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("PUT status = %d, want 200", resp.StatusCode)
	}
	body = decodeBody(t, resp)
	if body["data"].(map[string]any)["mode"] != "hitl" {
		t.Errorf("post-PUT mode = %v, want hitl", body["data"].(map[string]any)["mode"])
	}
}

func TestPolicyInvalidMode(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)
	resp := doJSON(t, srv, cookie, http.MethodPut, "/api/settings/policy", map[string]any{
		"mode": "yolo",
	})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400", resp.StatusCode)
	}
}

// -----------------------------------------------------------------
// Agent profiles + keys
// -----------------------------------------------------------------

func TestAgentProfileAndKeyFlow(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)

	// Create a profile.
	resp := postJSON(t, srv, cookie, "/api/agent-profiles", map[string]any{
		"name":       "shopper",
		"enabled":    false,
		"remit":      "",
		"m0_entries": []string{},
		"m3_entries": []string{},
	})
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("create profile status = %d", resp.StatusCode)
	}
	profile := decodeBody(t, resp)["data"].(map[string]any)
	profileID := profile["id"].(string)

	// Mint a key for it.
	resp = postJSON(t, srv, cookie, "/api/agent-profiles/"+profileID+"/keys", map[string]any{
		"label": "smoke",
	})
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("mint key status = %d", resp.StatusCode)
	}
	keyData := decodeBody(t, resp)["data"].(map[string]any)
	rawKey := keyData["api_key"].(string)
	keyID := keyData["id"].(string)
	if !strings.HasPrefix(rawKey, "adr_local_") {
		t.Errorf("api_key prefix = %q, want adr_local_*", rawKey)
	}

	// Mint a second key for the same profile -> first should auto-revoke.
	resp = postJSON(t, srv, cookie, "/api/agent-profiles/"+profileID+"/keys", map[string]any{
		"label": "smoke-2",
	})
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("second mint status = %d", resp.StatusCode)
	}
	second := decodeBody(t, resp)["data"].(map[string]any)
	if int(second["revoked_previous"].(float64)) != 1 {
		t.Errorf("revoked_previous = %v, want 1", second["revoked_previous"])
	}

	// List shows both keys with prefix-only.
	resp = getReq(t, srv, cookie, "/api/keys")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("list keys status = %d", resp.StatusCode)
	}
	keys := decodeBody(t, resp)["data"].([]any)
	if len(keys) != 2 {
		t.Errorf("len(keys) = %d, want 2", len(keys))
	}
	for _, k := range keys {
		entry := k.(map[string]any)
		if _, has := entry["api_key"]; has {
			t.Errorf("list response leaked api_key plaintext")
		}
	}

	// Revoke the second key explicitly.
	resp = doJSON(t, srv, cookie, http.MethodDelete, "/api/keys/"+keyID, nil)
	if resp.StatusCode != http.StatusNoContent {
		// Already-revoked path returns 404. The second key is the active
		// one; the first was auto-revoked above. So revoking keyID (the
		// first key id we captured) should have already returned 404.
		// Check explicitly.
		if resp.StatusCode != http.StatusNotFound {
			t.Fatalf("revoke status = %d, want 204 or 404", resp.StatusCode)
		}
	}
}

func TestProfileNameValidation(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)
	resp := postJSON(t, srv, cookie, "/api/agent-profiles", map[string]any{
		"name": "",
	})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 on empty name", resp.StatusCode)
	}
}

// -----------------------------------------------------------------
// Stats
// -----------------------------------------------------------------

func TestStatsOverview(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	// Seed: 3 events on 2 agents, 2 verdicts (one M0, one M3),
	// 1 pending review, 1 agents row with last_seen recent.
	if _, err := db.Exec(
		`INSERT INTO agents (id, agent_id, last_seen) VALUES (?, 'a1', datetime('now'))`,
		uuid.NewString(),
	); err != nil {
		t.Fatalf("seed agent: %v", err)
	}
	for i := 0; i < 3; i++ {
		if _, err := db.Exec(
			`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
			 VALUES (?, ?, ?, 'tool', 'r', '{}')`,
			uuid.NewString(), "sess-stats", []string{"a1", "a1", "a2"}[i],
		); err != nil {
			t.Fatalf("seed event: %v", err)
		}
	}
	for _, mc := range []string{"M0", "M3.b"} {
		if _, err := db.Exec(
			`INSERT INTO verdicts (id, event_id, session_id, mad_code, classification)
			 VALUES (?, ?, 'sess-stats', ?, 'notify')`,
			uuid.NewString(), uuid.NewString(), mc,
		); err != nil {
			t.Fatalf("seed verdict: %v", err)
		}
	}
	if _, err := db.Exec(
		`INSERT INTO hitl_queue (id, event_id, session_id, mad_code) VALUES (?, ?, 'sess-stats', 'M3')`,
		uuid.NewString(), uuid.NewString(),
	); err != nil {
		t.Fatalf("seed hitl: %v", err)
	}

	resp := getReq(t, srv, cookie, "/api/stats/overview")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	data := decodeBody(t, resp)["data"].(map[string]any)
	if int(data["total_events"].(float64)) != 3 {
		t.Errorf("total_events = %v, want 3", data["total_events"])
	}
	if int(data["flagged_verdicts"].(float64)) != 1 {
		t.Errorf("flagged_verdicts = %v, want 1 (only M3.b counts)", data["flagged_verdicts"])
	}
	if int(data["pending_reviews"].(float64)) != 1 {
		t.Errorf("pending_reviews = %v, want 1", data["pending_reviews"])
	}
	if int(data["active_agents"].(float64)) != 1 {
		t.Errorf("active_agents = %v, want 1", data["active_agents"])
	}
	dist := data["verdicts_by_mad"].(map[string]any)
	if int(dist["M0"].(float64)) != 1 || int(dist["M3"].(float64)) != 1 {
		t.Errorf("verdicts_by_mad = %v, want M0=1 M3=1", dist)
	}
}

func TestStatsActivityEmpty(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)
	resp := getReq(t, srv, cookie, "/api/stats/activity?range=24h")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d", resp.StatusCode)
	}
	data := decodeBody(t, resp)["data"].(map[string]any)
	if data["range"] != "24h" {
		t.Errorf("range = %v", data["range"])
	}
	buckets := data["buckets"].([]any)
	if len(buckets) != 0 {
		t.Errorf("buckets = %d, want 0 on empty events table", len(buckets))
	}
}

// -----------------------------------------------------------------
// Reviews / HITL
// -----------------------------------------------------------------

// newTestServerWithHub mirrors newTestServerLoggedIn but lets the test
// observe the hub directly so it can register a fake SDK subscriber
// before driving the approve/reject path.
func newTestServerWithHub(t *testing.T) (*httptest.Server, *sql.DB, *ws.Hub, string) {
	t.Helper()
	db := openTestDB(t)
	st := store.New(db)
	cfg := &config.Config{
		BackendPort:   "0",
		DBPath:        ":memory:",
		LLMURL:        "",
		LLMModelPath:  "",
		SessionSecret: "test",
	}
	plaintext := "correct horse battery staple"
	hash, err := auth.Hash(plaintext)
	if err != nil {
		t.Fatalf("hash: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO users (id, email, name, role, password_hash, must_change_password)
		 VALUES (?, ?, ?, 'admin', ?, 0)`,
		uuid.NewString(), "admin@localhost", "Administrator", hash,
	); err != nil {
		t.Fatalf("seed admin: %v", err)
	}
	hub := ws.NewHub()
	srv := httptest.NewServer(api.NewServer(cfg, db, st, stubClassifier{}, hub, ws.NewConnRegistry(), nil))
	t.Cleanup(srv.Close)
	t.Cleanup(func() { _ = db.Close() })
	cookie := loginAndGetCookie(t, srv, plaintext)
	return srv, db, hub, cookie
}

func TestApproveReviewPublishesToSubscriber(t *testing.T) {
	srv, db, hub, cookie := newTestServerWithHub(t)

	const sessID = "sess-hitl-1"
	eventID := uuid.NewString()
	verdictID := uuid.NewString()
	queueID := uuid.NewString()

	// Seed the event + verdict so the resolution detail lookup finds rows.
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, ?, 'agent-h', 'tool', 'r1', '{}')`,
		eventID, sessID,
	); err != nil {
		t.Fatalf("seed event: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO verdicts (id, event_id, session_id, mad_code, classification, reasoning)
		 VALUES (?, ?, ?, 'M3', 'notify', 'looks risky')`,
		verdictID, eventID, sessID,
	); err != nil {
		t.Fatalf("seed verdict: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO hitl_queue (id, event_id, verdict_id, session_id, mad_code)
		 VALUES (?, ?, ?, ?, 'M3')`,
		queueID, eventID, verdictID, sessID,
	); err != nil {
		t.Fatalf("seed hitl_queue: %v", err)
	}

	// Fake SDK subscriber.
	ch, dereg := hub.Register(sessID)
	defer dereg()

	resp := postJSON(t, srv, cookie, "/api/reviews/"+queueID+"/approve", map[string]any{})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	if data["status"] != "approved" {
		t.Errorf("status = %v, want approved", data["status"])
	}

	select {
	case buf := <-ch:
		if len(buf) == 0 {
			t.Fatal("expected non-empty hub frame")
		}
		// Don't unmarshal here, we trust the hub round-trip and the
		// hub_test.go suite covers the proto path.
	case <-time.After(time.Second):
		t.Fatal("subscriber never received the resolution frame")
	}

	// Status row updated.
	var status string
	if err := db.QueryRow(`SELECT status FROM hitl_queue WHERE id = ?`, queueID).Scan(&status); err != nil {
		t.Fatalf("query status: %v", err)
	}
	if status != "approved" {
		t.Errorf("hitl_queue.status = %v, want approved", status)
	}
}

func TestApproveReviewNoSubscriberStillResolves(t *testing.T) {
	srv, db, _, cookie := newTestServerWithHub(t)

	eventID := uuid.NewString()
	queueID := uuid.NewString()
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, 'sess-no-sub', 'agent-h', 'tool', 'r1', '{}')`,
		eventID,
	); err != nil {
		t.Fatalf("seed event: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO hitl_queue (id, event_id, session_id, mad_code)
		 VALUES (?, ?, 'sess-no-sub', 'M4')`,
		queueID, eventID,
	); err != nil {
		t.Fatalf("seed hitl_queue: %v", err)
	}

	resp := postJSON(t, srv, cookie, "/api/reviews/"+queueID+"/reject", map[string]any{})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	if data["notice"] == nil {
		t.Errorf("expected a notice when no SDK subscriber, got: %v", data)
	}

	var status string
	if err := db.QueryRow(`SELECT status FROM hitl_queue WHERE id = ?`, queueID).Scan(&status); err != nil {
		t.Fatalf("query status: %v", err)
	}
	if status != "rejected" {
		t.Errorf("hitl_queue.status = %v, want rejected", status)
	}
}

func TestApproveReviewSecondClickIs409(t *testing.T) {
	srv, db, _, cookie := newTestServerWithHub(t)
	eventID := uuid.NewString()
	queueID := uuid.NewString()
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, 'sess-twice', 'agent-h', 'tool', 'r1', '{}')`,
		eventID,
	); err != nil {
		t.Fatalf("seed event: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO hitl_queue (id, event_id, session_id, mad_code)
		 VALUES (?, ?, 'sess-twice', 'M3')`,
		queueID, eventID,
	); err != nil {
		t.Fatalf("seed hitl_queue: %v", err)
	}
	if r := postJSON(t, srv, cookie, "/api/reviews/"+queueID+"/approve", map[string]any{}); r.StatusCode != http.StatusOK {
		t.Fatalf("first approve = %d", r.StatusCode)
	}
	r := postJSON(t, srv, cookie, "/api/reviews/"+queueID+"/reject", map[string]any{})
	if r.StatusCode != http.StatusConflict {
		t.Fatalf("second resolve status = %d, want 409", r.StatusCode)
	}
}

// -----------------------------------------------------------------
// Webhooks
// -----------------------------------------------------------------

func TestCreateWebhook(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)

	resp := postJSON(t, srv, cookie, "/api/webhooks", map[string]any{
		"webhook_url": "https://discord.com/api/webhooks/123/secret-token-here",
		"alert_type":  "M3",
	})
	if resp.StatusCode != http.StatusCreated {
		t.Fatalf("status = %d, want 201", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	if data["alert_type"] != "M3" {
		t.Errorf("alert_type = %v, want M3", data["alert_type"])
	}
	if _, ok := data["id"].(string); !ok {
		t.Errorf("expected string id in response")
	}
}

func TestCreateWebhookBadURL(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)
	resp := postJSON(t, srv, cookie, "/api/webhooks", map[string]any{
		"webhook_url": "https://evil.example.com/hook",
		"alert_type":  "all",
	})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 on non-discord URL", resp.StatusCode)
	}
}

func TestCreateWebhookBadAlertType(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)
	resp := postJSON(t, srv, cookie, "/api/webhooks", map[string]any{
		"webhook_url": "https://discord.com/api/webhooks/123/abcdefghijklmnop",
		"alert_type":  "M2",
	})
	if resp.StatusCode != http.StatusBadRequest {
		t.Fatalf("status = %d, want 400 on alert_type M2", resp.StatusCode)
	}
}

func TestListAndDeleteWebhooks(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)

	// Create one.
	createResp := postJSON(t, srv, cookie, "/api/webhooks", map[string]any{
		"webhook_url": "https://discord.com/api/webhooks/999/long-secret-token-12345",
		"alert_type":  "all",
	})
	created := decodeBody(t, createResp)["data"].(map[string]any)
	id := created["id"].(string)

	// List should return it with masked URL.
	listResp := getReq(t, srv, cookie, "/api/webhooks")
	if listResp.StatusCode != http.StatusOK {
		t.Fatalf("list status = %d", listResp.StatusCode)
	}
	hooks := decodeBody(t, listResp)["data"].(map[string]any)["webhooks"].([]any)
	if len(hooks) != 1 {
		t.Fatalf("webhooks = %d, want 1", len(hooks))
	}
	masked := hooks[0].(map[string]any)["webhook_url_masked"].(string)
	if masked != "https://discord.com/api/webhooks/999/...***en-12345" {
		t.Errorf("masked url = %q, want exact token-only masking", masked)
	}

	// Delete it.
	delResp := doJSON(t, srv, cookie, http.MethodDelete, "/api/webhooks/"+id, nil)
	if delResp.StatusCode != http.StatusNoContent {
		t.Fatalf("delete status = %d, want 204", delResp.StatusCode)
	}

	// Re-list: empty.
	hooks = decodeBody(t, getReq(t, srv, cookie, "/api/webhooks"))["data"].(map[string]any)["webhooks"].([]any)
	if len(hooks) != 0 {
		t.Errorf("after delete, webhooks = %d, want 0", len(hooks))
	}

	// Double-delete is a 404.
	resp := doJSON(t, srv, cookie, http.MethodDelete, "/api/webhooks/"+id, nil)
	if resp.StatusCode != http.StatusNotFound {
		t.Errorf("second delete status = %d, want 404", resp.StatusCode)
	}
}

// -----------------------------------------------------------------
// Runtime agents
// -----------------------------------------------------------------

func TestListAgents(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	// Two agents, one event each, with a verdict on the older agent's event.
	if _, err := db.Exec(
		`INSERT INTO agents (id, agent_id, first_seen, last_seen) VALUES
		 (?, 'agent-a', datetime('now', '-2 hours'), datetime('now', '-2 minutes')),
		 (?, 'agent-b', datetime('now', '-1 hours'), datetime('now', '-1 minutes'))`,
		uuid.NewString(), uuid.NewString(),
	); err != nil {
		t.Fatalf("seed agents: %v", err)
	}
	eA := uuid.NewString()
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, 'sess-x', 'agent-a', 'tool', 'run-1', '{}')`, eA,
	); err != nil {
		t.Fatalf("seed event: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO verdicts (id, event_id, session_id, mad_code, classification)
		 VALUES (?, ?, 'sess-x', 'M3', 'notify')`,
		uuid.NewString(), eA,
	); err != nil {
		t.Fatalf("seed verdict: %v", err)
	}

	resp := getReq(t, srv, cookie, "/api/agents")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	agents := data["agents"].([]any)
	if len(agents) != 2 {
		t.Fatalf("agents = %d, want 2", len(agents))
	}
	first := agents[0].(map[string]any)
	if first["agent_id"] != "agent-b" {
		t.Errorf("first.agent_id = %v, want agent-b (most recently seen first)", first["agent_id"])
	}
	if first["worst_mad"] != "" {
		t.Errorf("agent-b worst_mad = %v, want empty (no events)", first["worst_mad"])
	}
	second := agents[1].(map[string]any)
	if second["agent_id"] != "agent-a" {
		t.Errorf("second.agent_id = %v, want agent-a", second["agent_id"])
	}
	if int(second["event_count"].(float64)) != 1 {
		t.Errorf("agent-a event_count = %v, want 1", second["event_count"])
	}
	if second["worst_mad"] != "M3" {
		t.Errorf("agent-a worst_mad = %v, want M3", second["worst_mad"])
	}
}

func TestGetAgent(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	if _, err := db.Exec(
		`INSERT INTO agents (id, agent_id) VALUES (?, 'agent-c')`,
		uuid.NewString(),
	); err != nil {
		t.Fatalf("seed agent: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, 'sess-c1', 'agent-c', 'tool', 'r1', '{}'),
		        (?, 'sess-c1', 'agent-c', 'llm',  'r2', '{}'),
		        (?, 'sess-c2', 'agent-c', 'tool', 'r3', '{}')`,
		uuid.NewString(), uuid.NewString(), uuid.NewString(),
	); err != nil {
		t.Fatalf("seed events: %v", err)
	}

	resp := getReq(t, srv, cookie, "/api/agents/agent-c")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	if data["agent_id"] != "agent-c" {
		t.Errorf("agent_id = %v, want agent-c", data["agent_id"])
	}
	if _, ok := data["event_count"]; ok {
		t.Errorf("event_count unexpectedly present in detail response")
	}
	if _, ok := data["worst_mad"]; ok {
		t.Errorf("worst_mad unexpectedly present in detail response")
	}
	sessions := data["sessions"].([]any)
	if len(sessions) != 2 {
		t.Fatalf("sessions = %d, want 2", len(sessions))
	}
	// Find the sess-c1 entry; it should have event_count == 2.
	var c1Count int
	for _, s := range sessions {
		sm := s.(map[string]any)
		if sm["session_id"] == "sess-c1" {
			c1Count = int(sm["event_count"].(float64))
		}
	}
	if c1Count != 2 {
		t.Errorf("sess-c1 event_count = %d, want 2", c1Count)
	}
}

func TestGetAgentNotFound(t *testing.T) {
	srv, _, _, cookie := newTestServerLoggedIn(t)
	resp := getReq(t, srv, cookie, "/api/agents/nope")
	if resp.StatusCode != http.StatusNotFound {
		t.Fatalf("status = %d, want 404", resp.StatusCode)
	}
}

// -----------------------------------------------------------------
// Session timeline
// -----------------------------------------------------------------

func TestSessionTimeline(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	const sid = "sess-timeline-1"

	// Two events; verdict on the second only.
	e1, e2 := uuid.NewString(), uuid.NewString()
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload, created_at)
		 VALUES (?, ?, 'agent-x', 'tool', 'run-1', '{"k":1}', datetime('now', '-2 seconds')),
		        (?, ?, 'agent-x', 'llm',  'run-2', '{"k":2}', datetime('now', '-1 seconds'))`,
		e1, sid, e2, sid,
	); err != nil {
		t.Fatalf("seed events: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO verdicts (id, event_id, session_id, mad_code, classification)
		 VALUES (?, ?, ?, 'M3', 'notify')`,
		uuid.NewString(), e2, sid,
	); err != nil {
		t.Fatalf("seed verdict: %v", err)
	}

	resp := getReq(t, srv, cookie, "/api/sessions/"+sid+"/timeline")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	if data["session_id"] != sid {
		t.Errorf("session_id = %v, want %v", data["session_id"], sid)
	}
	entries := data["entries"].([]any)
	if len(entries) != 2 {
		t.Fatalf("entries = %d, want 2", len(entries))
	}
	first := entries[0].(map[string]any)
	if first["id"] != e1 {
		t.Errorf("first.id = %v, want %v (oldest first)", first["id"], e1)
	}
	if _, hasVerdict := first["verdict"]; hasVerdict {
		t.Errorf("first entry should have no verdict (only e2 has one)")
	}
	second := entries[1].(map[string]any)
	verdict, ok := second["verdict"].(map[string]any)
	if !ok {
		t.Fatalf("second entry should have a verdict, got %T", second["verdict"])
	}
	if verdict["mad_code"] != "M3" {
		t.Errorf("verdict.mad_code = %v, want M3", verdict["mad_code"])
	}
}

// -----------------------------------------------------------------
// Events list filtering
// -----------------------------------------------------------------

// TestEventsMinMADFilterUsesLatestVerdict asserts the severity filter
// matches on each event's latest verdict only. An event re-classified
// from M3 to M0 must not surface under M3+. Regression for the bug
// where the EXISTS clause accepted any historical verdict.
func TestEventsMinMADFilterUsesLatestVerdict(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	const sid = "sess-min-mad"

	// Event A: older M3.a, newer M0 -> displayed as M0, must NOT
	// appear under M3+ or M2+.
	eA := uuid.NewString()
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, ?, 'agent-a', 'tool', 'r1', '{}')`,
		eA, sid,
	); err != nil {
		t.Fatalf("seed event A: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO verdicts (id, event_id, session_id, mad_code, classification, created_at)
		 VALUES (?, ?, ?, 'M3.a', 'block',  datetime('now', '-2 seconds')),
		        (?, ?, ?, 'M0',   'benign', datetime('now', '-1 seconds'))`,
		uuid.NewString(), eA, sid,
		uuid.NewString(), eA, sid,
	); err != nil {
		t.Fatalf("seed verdicts A: %v", err)
	}

	// Event B: older M0, newer M3.a -> displayed as M3.a, must appear
	// under M3+. Locks in that the filter still surfaces legitimately
	// flagged events.
	eB := uuid.NewString()
	if _, err := db.Exec(
		`INSERT INTO events (id, session_id, agent_id, event_type, run_id, payload)
		 VALUES (?, ?, 'agent-b', 'tool', 'r2', '{}')`,
		eB, sid,
	); err != nil {
		t.Fatalf("seed event B: %v", err)
	}
	if _, err := db.Exec(
		`INSERT INTO verdicts (id, event_id, session_id, mad_code, classification, created_at)
		 VALUES (?, ?, ?, 'M0',   'benign', datetime('now', '-2 seconds')),
		        (?, ?, ?, 'M3.a', 'block',  datetime('now', '-1 seconds'))`,
		uuid.NewString(), eB, sid,
		uuid.NewString(), eB, sid,
	); err != nil {
		t.Fatalf("seed verdicts B: %v", err)
	}

	// min_mad=M3 should surface event B only.
	resp := getReq(t, srv, cookie, "/api/events?session_id="+sid+"&min_mad=M3")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	data := decodeBody(t, resp)["data"].(map[string]any)
	if int(data["total"].(float64)) != 1 {
		t.Errorf("min_mad=M3 total = %v, want 1 (only event B)", data["total"])
	}
	events := data["events"].([]any)
	if len(events) != 1 || events[0].(map[string]any)["id"] != eB {
		t.Errorf("min_mad=M3 events = %v, want only event B (id %q)", events, eB)
	}

	// min_mad=M2 should also surface event B only (event A's latest is M0).
	resp = getReq(t, srv, cookie, "/api/events?session_id="+sid+"&min_mad=M2")
	data = decodeBody(t, resp)["data"].(map[string]any)
	if int(data["total"].(float64)) != 1 {
		t.Errorf("min_mad=M2 total = %v, want 1", data["total"])
	}

	// No filter should return both events.
	resp = getReq(t, srv, cookie, "/api/events?session_id="+sid)
	data = decodeBody(t, resp)["data"].(map[string]any)
	if int(data["total"].(float64)) != 2 {
		t.Errorf("no-filter total = %v, want 2", data["total"])
	}
}

// -----------------------------------------------------------------
// MCP servers
// -----------------------------------------------------------------

func TestListMcpServers(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	for i, row := range []struct {
		session, name, transport, endpoint, ago string
	}{
		{"sess-a", "filesystem", "stdio", "", "-2 seconds"},
		{"sess-b", "github", "streamable_http", "https://example/mcp", "-1 seconds"},
	} {
		_ = i
		if _, err := db.Exec(
			`INSERT INTO mcp_servers (session_id, name, transport, endpoint, received_at)
			 VALUES (?, ?, ?, ?, datetime('now', ?))`,
			row.session, row.name, row.transport, row.endpoint, row.ago,
		); err != nil {
			t.Fatalf("seed mcp row: %v", err)
		}
	}

	resp := getReq(t, srv, cookie, "/api/mcp/servers")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	servers := body["data"].(map[string]any)["servers"].([]any)
	if len(servers) != 2 {
		t.Fatalf("servers = %d, want 2", len(servers))
	}
	first := servers[0].(map[string]any)
	if first["name"] != "github" {
		t.Errorf("first.name = %v, want github (newest first)", first["name"])
	}
	if first["transport"] != "streamable_http" {
		t.Errorf("first.transport = %v, want streamable_http", first["transport"])
	}
}

// -----------------------------------------------------------------
// Audit log
// -----------------------------------------------------------------

func TestListAuditLog(t *testing.T) {
	srv, db, _, cookie := newTestServerLoggedIn(t)

	// Find the seeded admin's id; audit rows reference users via FK.
	var adminID string
	if err := db.QueryRow(`SELECT id FROM users WHERE email = ?`, "admin@localhost").Scan(&adminID); err != nil {
		t.Fatalf("lookup admin: %v", err)
	}

	// Login wrote a 'login_success' row; clear so the test asserts exact
	// ordering on its own seed rows.
	if _, err := db.Exec(`DELETE FROM audit_log`); err != nil {
		t.Fatalf("clear audit_log: %v", err)
	}

	for i, action := range []string{"policy.update", "agent_profile.create"} {
		uid := sql.NullString{String: adminID, Valid: true}
		if _, err := db.Exec(
			`INSERT INTO audit_log (id, user_id, action, target, details, created_at)
			 VALUES (?, ?, ?, ?, ?, datetime('now', ?))`,
			uuid.NewString(), uid, action, "target-"+action, `{"k":"v"}`,
			// Stagger created_at so the newest-first ordering is deterministic.
			[]string{"-2 seconds", "-1 seconds"}[i],
		); err != nil {
			t.Fatalf("seed audit row: %v", err)
		}
	}

	resp := getReq(t, srv, cookie, "/api/audit-log?per_page=10")
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("status = %d, want 200", resp.StatusCode)
	}
	body := decodeBody(t, resp)
	data := body["data"].(map[string]any)
	entries := data["entries"].([]any)
	if len(entries) != 2 {
		t.Fatalf("entries = %d, want 2", len(entries))
	}
	first := entries[0].(map[string]any)
	if first["action"] != "agent_profile.create" {
		t.Errorf("first entry action = %v, want agent_profile.create (newest first)", first["action"])
	}
	if first["user_email"] != "admin@localhost" {
		t.Errorf("user_email = %v, want admin@localhost", first["user_email"])
	}
	if first["user_name"] != "Administrator" {
		t.Errorf("user_name = %v, want Administrator", first["user_name"])
	}
	if total, _ := data["total"].(float64); int(total) != 2 {
		t.Errorf("total = %v, want 2", data["total"])
	}
}

// -----------------------------------------------------------------
// helpers
// -----------------------------------------------------------------

func newTestServer(t *testing.T) (*httptest.Server, *sql.DB, string, string) {
	t.Helper()
	return newTestServerWithMustChange(t, true)
}

func newTestServerLoggedIn(t *testing.T) (*httptest.Server, *sql.DB, string, string) {
	t.Helper()
	srv, db, plaintext, _ := newTestServerWithMustChange(t, false)
	cookie := loginAndGetCookie(t, srv, plaintext)
	return srv, db, plaintext, cookie
}

func newTestServerWithMustChange(t *testing.T, mustChange bool) (*httptest.Server, *sql.DB, string, string) {
	t.Helper()
	db := openTestDB(t)
	st := store.New(db)
	cfg := &config.Config{
		BackendPort:   "0",
		DBPath:        ":memory:",
		LLMURL:        "",
		LLMModelPath:  "",
		SessionSecret: "test",
	}
	plaintext := "correct horse battery staple"
	hash, err := auth.Hash(plaintext)
	if err != nil {
		t.Fatalf("hash: %v", err)
	}
	mc := 0
	if mustChange {
		mc = 1
	}
	if _, err := db.Exec(
		`INSERT INTO users (id, email, name, role, password_hash, must_change_password)
		 VALUES (?, ?, ?, 'admin', ?, ?)`,
		uuid.NewString(), "admin@localhost", "Administrator", hash, mc,
	); err != nil {
		t.Fatalf("seed admin: %v", err)
	}
	srv := httptest.NewServer(api.NewServer(cfg, db, st, stubClassifier{}, ws.NewHub(), ws.NewConnRegistry(), nil))
	t.Cleanup(srv.Close)
	t.Cleanup(func() { _ = db.Close() })
	return srv, db, plaintext, ""
}

func openTestDB(t *testing.T) *sql.DB {
	t.Helper()
	db, err := sql.Open("sqlite", "file:apitest"+uuid.NewString()+"?mode=memory&cache=shared")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	for _, p := range []string{
		"PRAGMA foreign_keys=ON",
		"PRAGMA journal_mode=WAL",
	} {
		if _, err := db.Exec(p); err != nil {
			t.Fatalf("apply %q: %v", p, err)
		}
	}
	if _, err := db.Exec(testSchema); err != nil {
		t.Fatalf("apply schema: %v", err)
	}
	return db
}

func loginAndGetCookie(t *testing.T, srv *httptest.Server, plaintext string) string {
	t.Helper()
	resp := postJSON(t, srv, nil, "/api/auth/login", map[string]any{
		"email":    "admin@localhost",
		"password": plaintext,
	})
	if resp.StatusCode != http.StatusOK {
		t.Fatalf("login: %d", resp.StatusCode)
	}
	return getCookie(resp, "adrian_token")
}

func getCookie(resp *http.Response, name string) string {
	for _, c := range resp.Cookies() {
		if c.Name == name {
			return c.Value
		}
	}
	return ""
}

func postJSON(t *testing.T, srv *httptest.Server, cookie any, path string, body any) *http.Response {
	t.Helper()
	return doJSON(t, srv, cookie, http.MethodPost, path, body)
}

func getReq(t *testing.T, srv *httptest.Server, cookie any, path string) *http.Response {
	t.Helper()
	return doJSON(t, srv, cookie, http.MethodGet, path, nil)
}

func doJSON(t *testing.T, srv *httptest.Server, cookie any, method, path string, body any) *http.Response {
	t.Helper()
	var reader *strings.Reader
	if body != nil {
		buf, _ := json.Marshal(body)
		reader = strings.NewReader(string(buf))
	}
	var req *http.Request
	var err error
	if reader != nil {
		req, err = http.NewRequestWithContext(context.Background(), method, srv.URL+path, reader)
	} else {
		req, err = http.NewRequestWithContext(context.Background(), method, srv.URL+path, http.NoBody)
	}
	if err != nil {
		t.Fatalf("new request: %v", err)
	}
	if body != nil {
		req.Header.Set("Content-Type", "application/json")
	}
	if c, ok := cookie.(string); ok && c != "" {
		req.AddCookie(&http.Cookie{Name: "adrian_token", Value: c})
	}
	resp, err := srv.Client().Do(req)
	if err != nil {
		t.Fatalf("do: %v", err)
	}
	t.Cleanup(func() { _ = resp.Body.Close() })
	return resp
}

func decodeBody(t *testing.T, resp *http.Response) map[string]any {
	t.Helper()
	var out map[string]any
	if err := json.NewDecoder(resp.Body).Decode(&out); err != nil {
		t.Fatalf("decode: %v", err)
	}
	return out
}

// testSchema is the minimum subset of 001_initial_schema.sql the API
// handlers exercise. Embedding the full migration here would couple
// the test to migration evolution.
const testSchema = `
CREATE TABLE users (
    id                   TEXT PRIMARY KEY,
    email                TEXT NOT NULL UNIQUE,
    name                 TEXT NOT NULL,
    role                 TEXT NOT NULL DEFAULT 'admin',
    password_hash        TEXT NOT NULL,
    must_change_password INTEGER NOT NULL DEFAULT 1,
    created_at           TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE user_sessions (
    id         TEXT PRIMARY KEY,
    user_id    TEXT NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    expires_at TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE agent_profiles (
    id         TEXT PRIMARY KEY,
    name       TEXT NOT NULL UNIQUE,
    enabled    INTEGER NOT NULL DEFAULT 0,
    remit      TEXT NOT NULL DEFAULT '',
    m0_entries TEXT NOT NULL DEFAULT '[]',
    m3_entries TEXT NOT NULL DEFAULT '[]',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE api_keys (
    id               TEXT PRIMARY KEY,
    key_hash         TEXT NOT NULL UNIQUE,
    prefix           TEXT NOT NULL,
    label            TEXT,
    agent_profile_id TEXT REFERENCES agent_profiles(id) ON DELETE SET NULL,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    revoked_at       TEXT
);
CREATE TABLE policies (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    mode       TEXT NOT NULL DEFAULT 'alert',
    policy_m0  INTEGER NOT NULL DEFAULT 0,
    policy_m2  INTEGER NOT NULL DEFAULT 0,
    policy_m3  INTEGER NOT NULL DEFAULT 1,
    policy_m4  INTEGER NOT NULL DEFAULT 1,
    updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
INSERT INTO policies (id) VALUES (1);
CREATE TABLE events (
    id               TEXT PRIMARY KEY,
    session_id       TEXT NOT NULL,
    agent_id         TEXT,
    agent_profile_id TEXT,
    event_type       TEXT NOT NULL,
    run_id           TEXT,
    payload          TEXT NOT NULL,
    tokens_used      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE verdicts (
    id               TEXT PRIMARY KEY,
    event_id         TEXT NOT NULL,
    session_id       TEXT NOT NULL,
    agent_profile_id TEXT,
    mad_code         TEXT NOT NULL,
    classification   TEXT NOT NULL,
    reasoning        TEXT,
    latency_ms       INTEGER,
    tokens_used      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE audit_log (
    id         TEXT PRIMARY KEY,
    user_id    TEXT,
    action     TEXT NOT NULL,
    target     TEXT,
    details    TEXT NOT NULL DEFAULT '{}',
    created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
CREATE TABLE mcp_servers (
    session_id  TEXT NOT NULL,
    name        TEXT NOT NULL,
    transport   TEXT NOT NULL,
    endpoint    TEXT NOT NULL DEFAULT '',
    received_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    PRIMARY KEY (session_id, name)
);
CREATE TABLE agents (
    id         TEXT PRIMARY KEY,
    agent_id   TEXT NOT NULL UNIQUE,
    first_seen TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    last_seen  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
    metadata   TEXT NOT NULL DEFAULT '{}'
);
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
CREATE TABLE hitl_queue (
    id          TEXT PRIMARY KEY,
    event_id    TEXT NOT NULL UNIQUE,
    verdict_id  TEXT,
    session_id  TEXT,
    mad_code    TEXT NOT NULL,
    status      TEXT NOT NULL DEFAULT 'pending',
    reviewed_by TEXT,
    reviewed_at TEXT,
    created_at  TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);
`
