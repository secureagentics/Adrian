// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws_test

import (
	"context"
	"crypto/sha256"
	"database/sql"
	"encoding/hex"
	"net/http"
	"net/http/httptest"
	"net/url"
	"strings"
	"sync/atomic"
	"testing"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	"google.golang.org/protobuf/proto"
	_ "modernc.org/sqlite"

	"github.com/secureagentics/Adrian/backend/internal/engine"
	bpb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
	"github.com/secureagentics/Adrian/backend/internal/ws"
)

// Round-trip smoke for the WS protocol.
//   - apply schema to an in-memory SQLite DB
//   - insert one api_keys row
//   - HTTP-mount AuthMiddleware + NewHandler under httptest.Server
//   - dial as a WebSocket client, send Authorization header
//   - send ClientFrame{login}, expect ServerFrame{login_ack}
//   - send ClientFrame{paired_batch}, expect ServerFrame{verdict}
//   - assert the verdicts row landed
func TestRoundTrip(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKey := "adr_local_test_key"
	keyHash := sha256Hex(plaintextKey)
	insertAPIKey(t, db, keyHash)

	// Switch to block mode so the gate forwards Verdicts to the SDK.
	// Alert mode (the schema default) is dashboard-only.
	if _, err := db.Exec(`UPDATE policies SET mode = 'block' WHERE id = 1`); err != nil {
		t.Fatalf("set mode=block: %v", err)
	}

	classifier := &fakeClassifier{}

	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, classifier, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	parsed, err := url.Parse(wsURL)
	if err != nil {
		t.Fatalf("parse ws url: %v", err)
	}

	dialer := websocket.DefaultDialer
	header := http.Header{"Authorization": {"Bearer " + plaintextKey}}
	conn, resp, err := dialer.Dial(parsed.String(), header)
	if err != nil {
		t.Fatalf("dial: %v (status=%v)", err, statusOrZero(resp))
	}
	t.Cleanup(func() { _ = conn.Close() })

	// 1. Send ClientFrame{login}.
	loginFrame := &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{
			Login: &bpb.SessionLogin{
				SessionId:     "test-session-1",
				LlmStack:      &bpb.LLMStack{Provider: "openai", Model: "gpt-4o"},
				SchemaVersion: 2,
			},
		},
	}
	if err := writeProto(conn, loginFrame); err != nil {
		t.Fatalf("send login: %v", err)
	}

	// 2. Expect ServerFrame{login_ack}.
	ack, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read login_ack: %v", err)
	}
	if ack.GetLoginAck() == nil {
		t.Fatalf("expected LoginAck, got %T", ack.Frame)
	}
	if ack.GetLoginAck().Policy.Mode != bpb.Mode_MODE_BLOCK {
		t.Fatalf("expected MODE_BLOCK, got %v", ack.GetLoginAck().Policy.Mode)
	}

	// 3. Send a paired_batch with one tool event.
	eventID := uuid.NewString()
	batchFrame := &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_PairedBatch{
			PairedBatch: &bpb.PairedEventBatch{
				Events: []*bpb.PairedEvent{{
					EventId:   eventID,
					SessionId: "test-session-1",
					RunId:     "run-1",
					PairType:  bpb.PairType_PAIR_TYPE_TOOL,
					Agent:     &bpb.AgentContext{AgentId: "test-agent"},
					Data: &bpb.PairedEvent_Tool{
						Tool: &bpb.ToolPairData{
							ToolName:   "noop",
							ToolCallId: "tc-1",
							Input:      `{"q":"hello"}`,
							Output:     "ok",
						},
					},
				}},
			},
		},
	}
	if err := writeProto(conn, batchFrame); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	// 4. Expect ServerFrame{verdict}.
	verdict, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read verdict: %v", err)
	}
	v := verdict.GetVerdict()
	if v == nil {
		t.Fatalf("expected Verdict, got %T", verdict.Frame)
	}
	if v.EventId != eventID {
		t.Fatalf("verdict event_id = %q, want %q", v.EventId, eventID)
	}
	if v.MadCode != "M0" {
		t.Fatalf("verdict mad_code = %q, want M0 (stub)", v.MadCode)
	}

	// 5. DB row landed.
	var n int
	if err := db.QueryRow("SELECT count(*) FROM verdicts WHERE event_id = ?", eventID).Scan(&n); err != nil {
		t.Fatalf("query verdicts: %v", err)
	}
	if n != 1 {
		t.Fatalf("expected 1 verdict row, got %d", n)
	}

	// 6. Runtime agents row was upserted.
	var seenAgentID string
	if err := db.QueryRow(`SELECT agent_id FROM agents WHERE agent_id = ?`, "test-agent").Scan(&seenAgentID); err != nil {
		t.Fatalf("expected agents row for 'test-agent': %v", err)
	}

	_ = conn.WriteMessage(websocket.CloseMessage,
		websocket.FormatCloseMessage(websocket.CloseNormalClosure, ""))
}

// Phase 4 anchor for issue #46: a classifier transport / HTTP failure
// is persisted + pushed as an ERROR verdict with no MAD code. The
// mode-specific fail-closed policy matrix is layered on in Phase 5.
func TestClassifierFailurePersistsAndPublishesErrorVerdict(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKey := "adr_local_test_key_classifier_failure"
	keyHash := sha256Hex(plaintextKey)
	insertAPIKey(t, db, keyHash)
	if _, err := db.Exec(`UPDATE policies SET mode = 'block' WHERE id = 1`); err != nil {
		t.Fatalf("set mode=block: %v", err)
	}

	llm := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "classifier exploded", http.StatusInternalServerError)
	}))
	t.Cleanup(llm.Close)
	classifier := engine.NewHTTPClient(llm.URL, "test-key", "test-model", nil, nil)

	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, classifier, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	header := http.Header{"Authorization": {"Bearer " + plaintextKey}}
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, header)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: "classifier-failure-sess", SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login: %v", err)
	}
	if _, err := readServerFrame(conn); err != nil {
		t.Fatalf("read login_ack: %v", err)
	}

	eventID := uuid.NewString()
	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_PairedBatch{PairedBatch: &bpb.PairedEventBatch{
			Events: []*bpb.PairedEvent{{
				EventId: eventID, SessionId: "classifier-failure-sess",
				RunId:    "run-classifier-failure",
				PairType: bpb.PairType_PAIR_TYPE_TOOL,
				Agent:    &bpb.AgentContext{AgentId: "failure-agent"},
				Data: &bpb.PairedEvent_Tool{Tool: &bpb.ToolPairData{
					ToolName: "noop", ToolCallId: "tc-classifier-failure", Input: "{}", Output: "ok",
				}},
			}},
		}},
	}); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	frame, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read verdict: %v", err)
	}
	verdict := frame.GetVerdict()
	if verdict == nil {
		t.Fatalf("expected Verdict, got %T", frame.Frame)
	}
	if verdict.MadCode != "" {
		t.Fatalf("pushed mad_code = %q, want empty on classifier error", verdict.MadCode)
	}
	if verdict.Status != bpb.VerdictStatus_VERDICT_STATUS_ERROR {
		t.Fatalf("pushed status = %v, want ERROR", verdict.Status)
	}

	var madCode, classification, verdictStatus, reasoning string
	if err := db.QueryRow(
		`SELECT mad_code, classification, verdict_status, reasoning FROM verdicts WHERE event_id = ?`,
		eventID,
	).Scan(&madCode, &classification, &verdictStatus, &reasoning); err != nil {
		t.Fatalf("query verdict: %v", err)
	}
	if madCode != "" || classification != "error" || verdictStatus != "error" {
		t.Fatalf("stored verdict = (%q, %q, %q), want ('', error, error)",
			madCode, classification, verdictStatus)
	}
	if !strings.Contains(reasoning, "classifier failure") ||
		!strings.Contains(reasoning, "post:") ||
		!strings.Contains(reasoning, "status 500") {
		t.Fatalf("stored reasoning = %q, want classifier failure with post/status 500", reasoning)
	}
}

func TestClassifierFailureAlertPersistsWithoutPublish(t *testing.T) {
	db, conn := classifierFailureConn(t, "alert", false)

	eventID := uuid.NewString()
	if err := sendPairedEvent(conn, classifierFailureToolEvent(eventID, "classifier-failure-alert")); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	if err := expectNoServerFrame(conn, 250*time.Millisecond); err == nil {
		t.Fatal("expected no SDK verdict in alert mode")
	}
	assertStoredErrorVerdict(t, db, eventID)
}

func TestClassifierFailureHitlFailClosedQueuesActionable(t *testing.T) {
	db, conn := classifierFailureConn(t, "hitl", true)

	eventID := uuid.NewString()
	if err := sendPairedEvent(conn, classifierFailureActionableEvent(eventID, "classifier-failure-hitl")); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	if err := expectNoServerFrame(conn, 250*time.Millisecond); err == nil {
		t.Fatal("expected actionable fail-closed ERROR verdict to be held for HITL")
	}
	assertStoredErrorVerdict(t, db, eventID)

	var queued int
	if err := db.QueryRow(
		`SELECT count(*) FROM hitl_queue h
		 JOIN verdicts v ON v.id = h.verdict_id
		 WHERE h.event_id = ? AND h.mad_code = '' AND v.verdict_status = 'error'`,
		eventID,
	).Scan(&queued); err != nil {
		t.Fatalf("query hitl_queue: %v", err)
	}
	if queued != 1 {
		t.Fatalf("queued error reviews = %d, want 1", queued)
	}
}

func TestClassifierFailureHitlFailClosedNonActionablePublishes(t *testing.T) {
	db, conn := classifierFailureConn(t, "hitl", true)

	eventID := uuid.NewString()
	if err := sendPairedEvent(conn, classifierFailureToolEvent(eventID, "classifier-failure-hitl-nonactionable")); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	frame, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read verdict: %v", err)
	}
	if got := frame.GetVerdict().GetStatus(); got != bpb.VerdictStatus_VERDICT_STATUS_ERROR {
		t.Fatalf("pushed status = %v, want ERROR", got)
	}
	assertStoredErrorVerdict(t, db, eventID)

	var queued int
	if err := db.QueryRow(`SELECT count(*) FROM hitl_queue WHERE event_id = ?`, eventID).Scan(&queued); err != nil {
		t.Fatalf("query hitl_queue: %v", err)
	}
	if queued != 0 {
		t.Fatalf("queued reviews = %d, want 0", queued)
	}
}

func TestClassifierFailureHitlQueueFailureFallsBackToPublish(t *testing.T) {
	db, conn := classifierFailureConn(t, "hitl", true)
	if _, err := db.Exec(`
CREATE TRIGGER fail_hitl_insert
BEFORE INSERT ON hitl_queue
BEGIN
    SELECT RAISE(FAIL, 'forced hitl insert failure');
END;
`); err != nil {
		t.Fatalf("create hitl failure trigger: %v", err)
	}

	eventID := uuid.NewString()
	if err := sendPairedEvent(conn, classifierFailureActionableEvent(eventID, "classifier-failure-hitl-fallback")); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	frame, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read verdict: %v", err)
	}
	verdict := frame.GetVerdict()
	if verdict.GetStatus() != bpb.VerdictStatus_VERDICT_STATUS_ERROR || verdict.GetMadCode() != "" {
		t.Fatalf("pushed verdict = (%q, %v), want ('', ERROR)", verdict.GetMadCode(), verdict.GetStatus())
	}
	assertStoredErrorVerdict(t, db, eventID)
}

func TestDuplicateEventRetryKeepsWSOpen(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKey := "adr_local_test_key_retry"
	keyHash := sha256Hex(plaintextKey)
	insertAPIKey(t, db, keyHash)
	if _, err := db.Exec(`UPDATE policies SET mode = 'block' WHERE id = 1`); err != nil {
		t.Fatalf("set mode=block: %v", err)
	}

	var classifyCalls int32
	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, &fakeClassifier{calls: &classifyCalls}, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	header := http.Header{"Authorization": {"Bearer " + plaintextKey}}
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, header)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: "retry-sess", SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login: %v", err)
	}
	if _, err := readServerFrame(conn); err != nil {
		t.Fatalf("read login_ack: %v", err)
	}

	eventID := uuid.NewString()
	batchFrame := &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_PairedBatch{PairedBatch: &bpb.PairedEventBatch{
			Events: []*bpb.PairedEvent{{
				EventId: eventID, SessionId: "retry-sess",
				RunId:    "run-retry",
				PairType: bpb.PairType_PAIR_TYPE_TOOL,
				Agent:    &bpb.AgentContext{AgentId: "retry-agent"},
				Data: &bpb.PairedEvent_Tool{Tool: &bpb.ToolPairData{
					ToolName: "noop", ToolCallId: "tc-retry", Input: "{}", Output: "ok",
				}},
			}},
		}},
	}

	if err := writeProto(conn, batchFrame); err != nil {
		t.Fatalf("send first paired_batch: %v", err)
	}
	firstVerdict, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read first verdict: %v", err)
	}
	if got := firstVerdict.GetVerdict().GetEventId(); got != eventID {
		t.Fatalf("first verdict event_id = %q, want %q", got, eventID)
	}

	if err := writeProto(conn, batchFrame); err != nil {
		t.Fatalf("send retry paired_batch: %v", err)
	}
	retryVerdict, err := readServerFrame(conn)
	if err != nil {
		t.Fatalf("read retry verdict after duplicate event insert: %v", err)
	}
	if got := retryVerdict.GetVerdict().GetEventId(); got != eventID {
		t.Fatalf("retry verdict event_id = %q, want %q", got, eventID)
	}

	var eventRows int
	if err := db.QueryRow("SELECT count(*) FROM events WHERE id = ?", eventID).Scan(&eventRows); err != nil {
		t.Fatalf("query events: %v", err)
	}
	if eventRows != 1 {
		t.Fatalf("expected duplicate retry to keep 1 event row, got %d", eventRows)
	}

	var verdictRows int
	if err := db.QueryRow("SELECT count(*) FROM verdicts WHERE event_id = ?", eventID).Scan(&verdictRows); err != nil {
		t.Fatalf("query verdicts: %v", err)
	}
	if verdictRows != 1 {
		t.Fatalf("expected duplicate retry to keep 1 verdict row, got %d", verdictRows)
	}
	if got := atomic.LoadInt32(&classifyCalls); got != 1 {
		t.Fatalf("expected classifier to run once for duplicate retry, got %d calls", got)
	}
}

// TestAlertModeNoFanOut confirms the mode gate withholds Verdict
// frames from the SDK in alert mode (dashboard-only). The verdict row
// is still persisted; only the WS write is suppressed.
func TestAlertModeNoFanOut(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKey := "adr_local_test_key_alert"
	keyHash := sha256Hex(plaintextKey)
	insertAPIKey(t, db, keyHash)
	// alert is the schema default; assert explicitly so the test
	// remains correct if the default ever changes.
	if _, err := db.Exec(`UPDATE policies SET mode = 'alert' WHERE id = 1`); err != nil {
		t.Fatalf("set mode=alert: %v", err)
	}

	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, &fakeClassifier{}, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	header := http.Header{"Authorization": {"Bearer " + plaintextKey}}
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, header)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: "alert-sess", SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login: %v", err)
	}
	if _, err := readServerFrame(conn); err != nil {
		t.Fatalf("read login_ack: %v", err)
	}

	eventID := uuid.NewString()
	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_PairedBatch{PairedBatch: &bpb.PairedEventBatch{
			Events: []*bpb.PairedEvent{{
				EventId: eventID, SessionId: "alert-sess",
				PairType: bpb.PairType_PAIR_TYPE_TOOL,
				Agent:    &bpb.AgentContext{AgentId: "alert-agent"},
				Data: &bpb.PairedEvent_Tool{Tool: &bpb.ToolPairData{
					ToolName: "noop", ToolCallId: "tc-1", Input: "{}", Output: "ok",
				}},
			}},
		}},
	}); err != nil {
		t.Fatalf("send paired_batch: %v", err)
	}

	// Read with a short deadline; alert mode holds the Verdict back, so
	// the read should time out instead of returning a frame.
	if err := conn.SetReadDeadline(time.Now().Add(500 * time.Millisecond)); err != nil {
		t.Fatalf("set deadline: %v", err)
	}
	if _, _, err := conn.ReadMessage(); err == nil {
		t.Fatal("expected read timeout in alert mode (no Verdict frame), got a frame")
	}

	// Verdict row was still persisted, alert mode only gates the SDK
	// write, not the storage path.
	var n int
	if err := db.QueryRow("SELECT count(*) FROM verdicts WHERE event_id = ?", eventID).Scan(&n); err != nil {
		t.Fatalf("query verdicts: %v", err)
	}
	if n != 1 {
		t.Fatalf("expected 1 verdict row even in alert mode, got %d", n)
	}
}

func TestSessionIDReuseDifferentOwnerDoesNotStealVerdicts(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKeyA := "adr_local_test_key_owner_a"
	plaintextKeyB := "adr_local_test_key_owner_b"
	insertAPIKeyWithProfile(t, db, sha256Hex(plaintextKeyA), "agent-profile-a")
	insertAPIKeyWithProfile(t, db, sha256Hex(plaintextKeyB), "agent-profile-b")

	if _, err := db.Exec(`UPDATE policies SET mode = 'block' WHERE id = 1`); err != nil {
		t.Fatalf("set mode=block: %v", err)
	}

	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, &fakeClassifier{}, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	connA, _, err := websocket.DefaultDialer.Dial(wsURL, http.Header{
		"Authorization": {"Bearer " + plaintextKeyA},
	})
	if err != nil {
		t.Fatalf("dial client A: %v", err)
	}
	t.Cleanup(func() { _ = connA.Close() })

	const sessionID = "shared-session-takeover-test"
	if err := writeProto(connA, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: sessionID, SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login client A: %v", err)
	}
	if _, err := readServerFrame(connA); err != nil {
		t.Fatalf("read login_ack client A: %v", err)
	}

	connB, _, err := websocket.DefaultDialer.Dial(wsURL, http.Header{
		"Authorization": {"Bearer " + plaintextKeyB},
	})
	if err != nil {
		t.Fatalf("dial client B: %v", err)
	}
	t.Cleanup(func() { _ = connB.Close() })
	if err := writeProto(connB, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: sessionID, SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login client B: %v", err)
	}
	if _, err := readServerFrame(connB); err != nil {
		t.Fatalf("read login_ack client B: %v", err)
	}
	if err := connB.SetReadDeadline(time.Now().Add(2 * time.Second)); err != nil {
		t.Fatalf("set client B deadline: %v", err)
	}
	if _, _, err := connB.ReadMessage(); err == nil {
		t.Fatal("expected conflicting client B to be closed")
	} else if closeErr, ok := err.(*websocket.CloseError); !ok || closeErr.Code != websocket.ClosePolicyViolation {
		t.Fatalf("client B close err = %v, want close code %d", err, websocket.ClosePolicyViolation)
	}

	eventID := uuid.NewString()
	if err := writeProto(connA, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_PairedBatch{PairedBatch: &bpb.PairedEventBatch{
			Events: []*bpb.PairedEvent{{
				EventId: eventID, SessionId: sessionID,
				PairType: bpb.PairType_PAIR_TYPE_TOOL,
				Agent:    &bpb.AgentContext{AgentId: "owner-a-agent"},
				Data: &bpb.PairedEvent_Tool{Tool: &bpb.ToolPairData{
					ToolName: "noop", ToolCallId: "tc-owner-a", Input: "{}", Output: "ok",
				}},
			}},
		}},
	}); err != nil {
		t.Fatalf("send paired_batch client A: %v", err)
	}

	verdict, err := readServerFrame(connA)
	if err != nil {
		t.Fatalf("read verdict client A: %v", err)
	}
	if got := verdict.GetVerdict(); got == nil || got.EventId != eventID {
		t.Fatalf("client A verdict = %+v, want event_id %q", got, eventID)
	}
}

// TestRevokeKicksLiveWS asserts the security guarantee: an open WS
// authenticated with key X gets terminated within seconds when X is
// revoked, not at next-disconnect-whenever. Drives the path the REST
// keys handler runs, registry.KickByKey(apiKeyID), against a live
// connection, then asserts ReadMessage fails with the kick's close
// code or the underlying socket close.
func TestRevokeKicksLiveWS(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKey := "adr_local_test_key_kick"
	keyHash := sha256Hex(plaintextKey)
	insertAPIKey(t, db, keyHash)

	apiKey, err := st.LookupAPIKey(context.Background(), keyHash)
	if err != nil {
		t.Fatalf("lookup api key: %v", err)
	}

	registry := ws.NewConnRegistry()
	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(
		ws.NewHandler(st, &fakeClassifier{}, ws.NewHub(), registry, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	header := http.Header{"Authorization": {"Bearer " + plaintextKey}}
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, header)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	// Round-trip a login so handleLogin returns and the deferred
	// registry.Register has fired. Without this we'd race the kick
	// against the registration.
	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: "kick-sess", SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login: %v", err)
	}
	if _, err := readServerFrame(conn); err != nil {
		t.Fatalf("read login_ack: %v", err)
	}

	// Revoke + kick. Same two-step the REST keys handler runs.
	if err := st.RevokeAPIKey(context.Background(), apiKey.ID); err != nil {
		t.Fatalf("revoke: %v", err)
	}
	if n := registry.KickByKey(apiKey.ID); n != 1 {
		t.Fatalf("KickByKey returned %d kicked conns, want 1", n)
	}

	_ = conn.SetReadDeadline(time.Now().Add(3 * time.Second))
	_, _, err = conn.ReadMessage()
	if err == nil {
		t.Fatal("expected ReadMessage error after kick, got nil")
	}
	if closeErr, ok := err.(*websocket.CloseError); ok {
		if closeErr.Code != 4401 {
			t.Errorf("close code = %d, want 4401 (key revoked)", closeErr.Code)
		}
		return
	}
	// Some Go runtimes surface the kick as a transport error rather
	// than a clean Close frame. Either is acceptable proof of the
	// kick, the security property is "the read no longer succeeds".
	msg := err.Error()
	if !strings.Contains(msg, "use of closed") &&
		!strings.Contains(msg, "EOF") &&
		!strings.Contains(msg, "connection reset") &&
		!strings.Contains(msg, "broken pipe") {
		t.Fatalf("post-kick read err = %v (want CloseError 4401 or socket-close)", err)
	}
}

// TestKickIsIdempotent asserts that calling KickByKey on a key with
// no live connections (already kicked, never connected, or stale id)
// is a no-op returning 0, the REST handlers call this unconditionally
// and must not blow up on an unconnected key.
func TestKickIsIdempotent(t *testing.T) {
	registry := ws.NewConnRegistry()
	if n := registry.KickByKey("never-seen-this-id"); n != 0 {
		t.Errorf("KickByKey on absent id returned %d, want 0", n)
	}
}

func TestUnauthDial(t *testing.T) {
	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, &fakeClassifier{}, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	dialer := websocket.DefaultDialer

	// No Authorization header at all.
	_, resp, err := dialer.Dial(wsURL, nil)
	if err == nil {
		t.Fatal("expected dial error on missing auth")
	}
	if resp == nil || resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %v", statusOrZero(resp))
	}

	// Wrong bearer.
	header := http.Header{"Authorization": {"Bearer wrong-key"}}
	_, resp, err = dialer.Dial(wsURL, header)
	if err == nil {
		t.Fatal("expected dial error on wrong bearer")
	}
	if resp == nil || resp.StatusCode != http.StatusUnauthorized {
		t.Fatalf("expected 401, got %v", statusOrZero(resp))
	}
}

// -----------------------------------------------------------------
// helpers
// -----------------------------------------------------------------

type fakeClassifier struct {
	calls *int32
}

func classifierFailureConn(t *testing.T, mode string, failClosed bool) (*sql.DB, *websocket.Conn) {
	t.Helper()

	db := openInMemoryDB(t)
	t.Cleanup(func() { _ = db.Close() })

	st := store.New(db)
	plaintextKey := "adr_local_test_key_classifier_failure_" + uuid.NewString()
	keyHash := sha256Hex(plaintextKey)
	insertAPIKey(t, db, keyHash)

	failClosedInt := 0
	if failClosed {
		failClosedInt = 1
	}
	if _, err := db.Exec(
		`UPDATE policies SET mode = ?, fail_closed_on_classifier_error = ? WHERE id = 1`,
		mode, failClosedInt,
	); err != nil {
		t.Fatalf("set policy: %v", err)
	}

	llm := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "classifier exploded", http.StatusInternalServerError)
	}))
	t.Cleanup(llm.Close)
	classifier := engine.NewHTTPClient(llm.URL, "test-key", "test-model", nil, nil)

	mux := http.NewServeMux()
	mux.Handle("/ws", ws.AuthMiddleware(st)(ws.NewHandler(st, classifier, ws.NewHub(), nil, nil)))
	srv := httptest.NewServer(mux)
	t.Cleanup(srv.Close)

	wsURL := "ws" + strings.TrimPrefix(srv.URL, "http") + "/ws"
	header := http.Header{"Authorization": {"Bearer " + plaintextKey}}
	conn, _, err := websocket.DefaultDialer.Dial(wsURL, header)
	if err != nil {
		t.Fatalf("dial: %v", err)
	}
	t.Cleanup(func() { _ = conn.Close() })

	if err := writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_Login{Login: &bpb.SessionLogin{
			SessionId: "classifier-failure-sess-" + uuid.NewString(), SchemaVersion: 2,
		}},
	}); err != nil {
		t.Fatalf("send login: %v", err)
	}
	if _, err := readServerFrame(conn); err != nil {
		t.Fatalf("read login_ack: %v", err)
	}
	return db, conn
}

func classifierFailureToolEvent(eventID, sessionID string) *bpb.PairedEvent {
	return &bpb.PairedEvent{
		EventId: eventID, SessionId: sessionID,
		RunId:    "run-classifier-failure",
		PairType: bpb.PairType_PAIR_TYPE_TOOL,
		Agent:    &bpb.AgentContext{AgentId: "failure-agent"},
		Data: &bpb.PairedEvent_Tool{Tool: &bpb.ToolPairData{
			ToolName: "noop", ToolCallId: "tc-classifier-failure", Input: "{}", Output: "ok",
		}},
	}
}

func classifierFailureActionableEvent(eventID, sessionID string) *bpb.PairedEvent {
	return &bpb.PairedEvent{
		EventId: eventID, SessionId: sessionID,
		RunId:    "run-classifier-failure",
		PairType: bpb.PairType_PAIR_TYPE_LLM,
		Agent:    &bpb.AgentContext{AgentId: "failure-agent"},
		Data: &bpb.PairedEvent_Llm{Llm: &bpb.LlmPairData{
			Model:  "test-model",
			Output: "calling tool",
			ToolCalls: []*bpb.ToolCall{{
				Name: "noop", Id: "tc-classifier-failure", Args: "{}",
			}},
		}},
	}
}

func sendPairedEvent(conn *websocket.Conn, ev *bpb.PairedEvent) error {
	return writeProto(conn, &bpb.ClientFrame{
		Frame: &bpb.ClientFrame_PairedBatch{PairedBatch: &bpb.PairedEventBatch{
			Events: []*bpb.PairedEvent{ev},
		}},
	})
}

func expectNoServerFrame(conn *websocket.Conn, timeout time.Duration) error {
	if err := conn.SetReadDeadline(time.Now().Add(timeout)); err != nil {
		return err
	}
	_, _, err := conn.ReadMessage()
	_ = conn.SetReadDeadline(time.Time{})
	return err
}

func assertStoredErrorVerdict(t *testing.T, db *sql.DB, eventID string) {
	t.Helper()
	var madCode, classification, verdictStatus string
	if err := db.QueryRow(
		`SELECT mad_code, classification, verdict_status FROM verdicts WHERE event_id = ?`,
		eventID,
	).Scan(&madCode, &classification, &verdictStatus); err != nil {
		t.Fatalf("query verdict: %v", err)
	}
	if madCode != "" || classification != "error" || verdictStatus != "error" {
		t.Fatalf("stored verdict = (%q, %q, %q), want ('', error, error)",
			madCode, classification, verdictStatus)
	}
}

func (f *fakeClassifier) Classify(_ context.Context, _ *bpb.PairedEvent, _ string) (*engine.Verdict, error) {
	if f.calls != nil {
		atomic.AddInt32(f.calls, 1)
	}
	return &engine.Verdict{MADCode: "M0", Classification: "benign"}, nil
}

func (f *fakeClassifier) Ping(_ context.Context) error { return nil }

func openInMemoryDB(t *testing.T) *sql.DB {
	t.Helper()
	// Shared cache + named DSN so all goroutines see the same in-mem DB.
	db, err := sql.Open("sqlite", "file:wstest?mode=memory&cache=shared")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
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

func insertAPIKey(t *testing.T, db *sql.DB, hashHex string) {
	t.Helper()
	_, err := db.Exec(
		`INSERT INTO api_keys (id, key_hash, prefix, label) VALUES (?, ?, ?, ?)`,
		uuid.NewString(), hashHex, "adr_local_te", "test",
	)
	if err != nil {
		t.Fatalf("insert api_keys: %v", err)
	}
}

func insertAPIKeyWithProfile(t *testing.T, db *sql.DB, hashHex, agentProfileID string) {
	t.Helper()
	_, err := db.Exec(
		`INSERT INTO api_keys (id, key_hash, prefix, label, agent_profile_id) VALUES (?, ?, ?, ?, ?)`,
		uuid.NewString(), hashHex, "adr_local_te", "test", agentProfileID,
	)
	if err != nil {
		t.Fatalf("insert api_keys: %v", err)
	}
}

func writeProto(conn *websocket.Conn, msg proto.Message) error {
	buf, err := proto.Marshal(msg)
	if err != nil {
		return err
	}
	return conn.WriteMessage(websocket.BinaryMessage, buf)
}

func readServerFrame(conn *websocket.Conn) (*bpb.ServerFrame, error) {
	if err := conn.SetReadDeadline(time.Now().Add(5 * time.Second)); err != nil {
		return nil, err
	}
	_, raw, err := conn.ReadMessage()
	if err != nil {
		return nil, err
	}
	var sf bpb.ServerFrame
	if err := proto.Unmarshal(raw, &sf); err != nil {
		return nil, err
	}
	return &sf, nil
}

func sha256Hex(s string) string {
	sum := sha256.Sum256([]byte(s))
	return hex.EncodeToString(sum[:])
}

func statusOrZero(r *http.Response) int {
	if r == nil {
		return 0
	}
	return r.StatusCode
}

// testSchema is the minimum subset of 001_initial_schema.sql the WS
// handler exercises (api_keys, policies, events, verdicts, mcp_servers,
// hitl_queue).
// Embedding the full migration file here would couple the test to the
// migration's evolution.
const testSchema = `
CREATE TABLE api_keys (
    id               TEXT PRIMARY KEY,
    key_hash         TEXT NOT NULL UNIQUE,
    prefix           TEXT NOT NULL,
    label            TEXT,
    agent_profile_id TEXT,
    revoked_at       TEXT
);
CREATE TABLE policies (
    id         INTEGER PRIMARY KEY CHECK (id = 1),
    mode       TEXT NOT NULL DEFAULT 'alert',
    policy_m0  INTEGER NOT NULL DEFAULT 0,
    policy_m2  INTEGER NOT NULL DEFAULT 0,
    policy_m3  INTEGER NOT NULL DEFAULT 1,
    policy_m4  INTEGER NOT NULL DEFAULT 1,
    fail_closed_on_classifier_error INTEGER NOT NULL DEFAULT 0,
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
    verdict_status   TEXT NOT NULL DEFAULT 'ok',
    reasoning        TEXT,
    latency_ms       INTEGER,
    tokens_used      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
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
