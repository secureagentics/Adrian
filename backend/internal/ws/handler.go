// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// Package ws implements the SDK <-> backend WebSocket protocol.
//
// Wire format:
//   - Authorization: Bearer <api_key> on the upgrade request.
//   - Binary messages carry serialised pb.ClientFrame / pb.ServerFrame.
//   - First client frame must be ClientFrame.login (SessionLogin).
//   - Server replies once with ServerFrame.login_ack (LoginAck).
//   - Subsequent client frames are paired_batch or mcp_inventory.
//   - Server sends ServerFrame.verdict per ingested event.
package ws

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"github.com/google/uuid"
	"github.com/gorilla/websocket"
	"google.golang.org/protobuf/proto"

	"github.com/secureagentics/Adrian/backend/internal/engine"
	pb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

var upgrader = websocket.Upgrader{
	// Auth has already happened in the AuthMiddleware that fronts this
	// handler; same-origin enforcement is not applicable to a local
	// SDK -> backend connection. The dashboard's session-cookie API
	// will get its own CSRF-aware auth path.
	CheckOrigin: func(r *http.Request) bool { return true },
}

// VerdictHook fires once per classified event, after the verdict is
// persisted and dispatched. The notifications dispatcher subscribes
// via main wiring; pass nil to skip notification fan-out (tests).
type VerdictHook func(eventID, sessionID, agentID, madCode, classification string)

// NewHandler returns the WebSocket handler. AuthMiddleware must wrap
// this so the api-key row is available via authedKey(ctx).
//
// hub serialises server-pushed frames per session_id and is the
// channel REST review approve/reject uses to ride a HITL resolution
// back to the SDK. Required.
//
// registry tracks live connections per api_key_id so revoke / rotate
// can terminate them. May be nil only in tests that don't exercise
// revocation; production wiring always supplies one.
//
// hook is called per verdict (regardless of whether the SDK was
// notified). Pass nil when no observer is wired.
func NewHandler(s *store.Store, classifier engine.Classifier, hub *Hub, registry *ConnRegistry, hook VerdictHook) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		key := authedKey(r.Context())
		if key == nil {
			http.Error(w, "missing auth context", http.StatusInternalServerError)
			return
		}

		conn, err := upgrader.Upgrade(w, r, nil)
		if err != nil {
			slog.WarnContext(r.Context(), "ws.upgrade_failed", "error", err)
			return
		}
		defer conn.Close()

		// Register this connection so a /api/agent-profiles/.../keys
		// rotate or /api/keys/{id} revoke can terminate it
		// immediately. The deregister fires before conn.Close() so
		// the registry never holds a pointer to a closed conn.
		if registry != nil {
			deregister := registry.Register(key.ID, conn)
			defer deregister()
		}

		sess := &session{apiKey: key}
		serve(r.Context(), conn, sess, s, classifier, hub, hook)
	}
}

func serve(ctx context.Context, conn *websocket.Conn, sess *session, st *store.Store, classifier engine.Classifier, hub *Hub, hook VerdictHook) {
	if err := handleLogin(ctx, conn, sess, st); err != nil {
		slog.WarnContext(ctx, "ws.login_failed", "error", err, "api_key_id", sess.apiKey.ID)
		return
	}
	slog.InfoContext(ctx, "ws.session_open",
		"session_id", sess.sessionID,
		"api_key_id", sess.apiKey.ID,
		"llm_provider", sess.llmProvider,
		"llm_model", sess.llmModel,
	)

	// Register for server-pushed frames AFTER login: classified
	// verdicts and HITL resolutions land here, drained by the writer
	// goroutine. The LoginAck is written directly inside handleLogin
	// (single goroutine, pre-register, no concurrency to serialise).
	hubCh, deregister, err := hub.Register(sess.sessionID, sess.routeOwner())
	if err != nil {
		if errors.Is(err, ErrSessionOwnerConflict) {
			slog.WarnContext(ctx, "ws.session_owner_conflict",
				"session_id", sess.sessionID,
				"api_key_id", sess.apiKey.ID,
				"route_owner", sess.routeOwner(),
			)
			closeWith(conn, closePolicyViolation, "session_id already active for another owner")
			return
		}
		slog.ErrorContext(ctx, "ws.session_register_failed",
			"error", err,
			"session_id", sess.sessionID,
			"api_key_id", sess.apiKey.ID,
		)
		closeWith(conn, closeInternalServerErr, "internal error")
		return
	}
	writerDone := make(chan struct{})
	go func() {
		defer close(writerDone)
		for buf := range hubCh {
			if err := conn.WriteMessage(websocket.BinaryMessage, buf); err != nil {
				return
			}
		}
	}()
	// Deregister closes hubCh so the writer goroutine exits; we then
	// wait for it before falling out of serve so conn.Close() (run by
	// the caller) doesn't race with an in-flight WriteMessage.
	defer func() {
		deregister()
		<-writerDone
	}()

	for {
		mt, raw, err := conn.ReadMessage()
		if err != nil {
			if !websocket.IsCloseError(err, websocket.CloseNormalClosure, websocket.CloseGoingAway) {
				slog.InfoContext(ctx, "ws.read_loop_exit", "error", err, "session_id", sess.sessionID)
			}
			return
		}
		if mt != websocket.BinaryMessage {
			closeWith(conn, closeProtocolError, "expected binary frame")
			return
		}
		var frame pb.ClientFrame
		if err := proto.Unmarshal(raw, &frame); err != nil {
			closeWith(conn, closeProtocolError, "malformed ClientFrame")
			return
		}

		switch f := frame.Frame.(type) {
		case *pb.ClientFrame_Login:
			slog.DebugContext(ctx, "ws.login_repeat_ignored", "session_id", sess.sessionID)
		case *pb.ClientFrame_PairedBatch:
			if err := handlePairedBatch(ctx, sess, st, classifier, hub, hook, f.PairedBatch); err != nil {
				slog.ErrorContext(ctx, "ws.paired_batch_failed",
					"error", err, "session_id", sess.sessionID)
				closeWith(conn, closeInternalServerErr, "internal error")
				return
			}
		case *pb.ClientFrame_McpInventory:
			if err := handleMcpInventory(ctx, sess, st, f.McpInventory); err != nil {
				slog.ErrorContext(ctx, "ws.mcp_inventory_failed",
					"error", err, "session_id", sess.sessionID)
				closeWith(conn, closeInternalServerErr, "internal error")
				return
			}
		default:
			closeWith(conn, closeProtocolError, "unknown frame type")
			return
		}
	}
}

func handleLogin(ctx context.Context, conn *websocket.Conn, sess *session, st *store.Store) error {
	if err := conn.SetReadDeadline(time.Now().Add(15 * time.Second)); err != nil {
		return err
	}
	defer conn.SetReadDeadline(time.Time{})

	mt, raw, err := conn.ReadMessage()
	if err != nil {
		return err
	}
	if mt != websocket.BinaryMessage {
		closeWith(conn, closeProtocolError, "expected binary frame")
		return errors.New("non-binary first frame")
	}

	var frame pb.ClientFrame
	if err := proto.Unmarshal(raw, &frame); err != nil {
		closeWith(conn, closeProtocolError, "malformed ClientFrame")
		return err
	}
	loginField, ok := frame.Frame.(*pb.ClientFrame_Login)
	if !ok || loginField.Login == nil {
		closeWith(conn, closeProtocolError, "first frame must be SessionLogin")
		return errors.New("first frame is not SessionLogin")
	}
	login := loginField.Login

	if login.SchemaVersion != schemaVersion {
		closeWith(conn, closeProtocolError, "unsupported schema_version")
		return errors.New("schema_version mismatch")
	}

	sess.sessionID = login.SessionId
	if login.LlmStack != nil {
		sess.llmProvider = login.LlmStack.Provider
		sess.llmModel = login.LlmStack.Model
	}
	sess.source = login.GetSource()
	sess.loggedIn = true

	pol, err := st.GetPolicy(ctx)
	if err != nil {
		closeWith(conn, closeInternalServerErr, "policy unavailable")
		return err
	}
	ack := &pb.ServerFrame{
		Frame: &pb.ServerFrame_LoginAck{
			LoginAck: &pb.LoginAck{
				Policy: PolicySnapshot(pol),
			},
		},
	}
	out, err := proto.Marshal(ack)
	if err != nil {
		closeWith(conn, closeInternalServerErr, "marshal failed")
		return err
	}
	return conn.WriteMessage(websocket.BinaryMessage, out)
}

func handlePairedBatch(ctx context.Context, sess *session, st *store.Store, classifier engine.Classifier, hub *Hub, hook VerdictHook, batch *pb.PairedEventBatch) error {
	if batch == nil {
		return nil
	}
	pol, err := st.GetPolicy(ctx)
	if err != nil {
		return err
	}
	snap := PolicySnapshot(pol)

	for _, ev := range batch.Events {
		if err := persistAndClassify(ctx, sess, st, classifier, hub, hook, ev, snap); err != nil {
			return err
		}
	}
	return nil
}

func persistAndClassify(ctx context.Context, sess *session, st *store.Store, classifier engine.Classifier, hub *Hub, hook VerdictHook, ev *pb.PairedEvent, snap *pb.PolicySnapshot) error {
	payloadJSON, err := pairedEventToJSON(ev)
	if err != nil {
		return err
	}
	inserted, err := st.InsertEvent(ctx, newEventRow(sess, ev, payloadJSON))
	if err != nil {
		return err
	}
	if !inserted {
		// A retry can race with the first in-flight delivery: event row
		// already exists, but verdict insert has not landed yet. Poll a
		// few times before giving up so we don't fail the WS batch on a
		// benign duplicate.
		for i := 0; i < 3; i++ {
			existing, err := st.GetVerdictByEventID(ctx, ev.EventId)
			if err == nil {
				return dispatchVerdict(ctx, sess, st, hub, ev, snap, existing.ID, existing.MADCode, existing.VerdictStatus)
			}
			if !errors.Is(err, store.ErrNotFound) {
				return err
			}
			select {
			case <-ctx.Done():
				return ctx.Err()
			case <-time.After(25 * time.Millisecond):
			}
		}
		return nil
	}

	// Refresh the runtime agents row so the dashboard's /agents view
	// reflects activity. Best-effort: a write failure is logged but
	// doesn't break the classify path.
	if agentID := ev.GetAgent().GetAgentId(); agentID != "" {
		if err := st.UpsertAgent(ctx, agentID); err != nil {
			slog.WarnContext(ctx, "agents.upsert_failed", "error", err, "agent_id", agentID)
		}
	}

	// agentProfileID is bound to the API key at WS-login time. Empty
	// when the key wasn't tied to a profile, the classifier renders
	// against the generic remit in that case.
	agentProfileID := ""
	if id := sess.agentProfileID(); id != nil {
		agentProfileID = *id
	}
	verdict, err := classifier.Classify(ctx, ev, agentProfileID)
	if err != nil {
		slog.WarnContext(ctx, "ws.classifier_failure",
			"error", err, "event_id", ev.EventId)
		reasoning := "classifier failure: " + err.Error()
		vrow := &store.Verdict{
			ID:             uuid.NewString(),
			EventID:        ev.EventId,
			SessionID:      sess.sessionID,
			AgentProfileID: sess.agentProfileID(),
			MADCode:        "",
			Classification: "error",
			VerdictStatus:  "error",
			Reasoning:      &reasoning,
			TokensUsed:     0,
		}
		if err := st.InsertVerdict(ctx, vrow); err != nil {
			return err
		}
		if hook != nil {
			hook(ev.EventId, sess.sessionID, ev.GetAgent().GetAgentId(), "", "error")
		}
		return dispatchVerdict(ctx, sess, st, hub, ev, snap, vrow.ID, "", "error")
	}

	vrow := &store.Verdict{
		ID:             uuid.NewString(),
		EventID:        ev.EventId,
		SessionID:      sess.sessionID,
		AgentProfileID: sess.agentProfileID(),
		MADCode:        verdict.MADCode,
		Classification: verdict.Classification,
		VerdictStatus:  "ok",
		Reasoning:      strPtrOrNil(verdict.Reasoning),
		LatencyMS:      int64PtrIfNonZero(verdict.LatencyMS),
		TokensUsed:     0,
	}
	if err := st.InsertVerdict(ctx, vrow); err != nil {
		return err
	}

	// Notify observers (Discord dispatcher, future webhooks). Fires for
	// every verdict regardless of execution mode, operators want to
	// see flagged events even in alert mode where the SDK isn't paused.
	if hook != nil {
		hook(ev.EventId, sess.sessionID, ev.GetAgent().GetAgentId(),
			verdict.MADCode, verdict.Classification)
	}

	return dispatchVerdict(ctx, sess, st, hub, ev, snap, vrow.ID, verdict.MADCode, "ok")
}

func dispatchVerdict(ctx context.Context, sess *session, st *store.Store, hub *Hub, ev *pb.PairedEvent, snap *pb.PolicySnapshot, verdictID, madCode, verdictStatus string) error {
	// Mode-gated dispatch:
	//   alert: persist verdict, do NOT notify the SDK (dashboard-only).
	//   hitl + in-scope + actionable: persist + queue for human review,
	//     hold the SDK frame until /api/reviews/{id}/{approve|reject}.
	//   hitl + in-scope + non-actionable: forward (review would be a
	//     no-op for the operator since the SDK never blocks on it).
	//   hitl + out-of-scope: forward (no review queued for this code).
	//   block: forward all verdicts; SDK is the enforcement point.
	inScope := shouldFanOut(snap, madCode)
	switch snap.GetMode() {
	case pb.Mode_MODE_ALERT:
		// Dashboard-only: the verdict is persisted, the SDK is not
		// notified. No SDK-side action is expected in alert mode.
		return nil
	case pb.Mode_MODE_HITL:
		// Claude Code gates HITL client-side: its PreToolUse hook blocks and
		// shows a terminal approval prompt, so CC is its own wait point.
		// Forward every in-scope verdict to it and never queue a dashboard
		// review no operator would action. This is source-gated and
		// independent of isActionable, which only models the LangChain wait
		// point. Other SDKs are held for review only when they have a
		// server-visible wait point (isActionable); anything else forwards so
		// the SDK isn't left waiting on a resolution that will never come.
		if inScope && sess.source != "claude-code" && isActionable(ev) {
			// Queue for human review and hold the verdict. The reviews REST
			// handler resumes the SDK with a HitlResponse-bearing Verdict on
			// approve/reject via the same hub channel.
			if err := st.InsertHitlQueue(ctx, ev.EventId, verdictID, sess.sessionID, madCode); err != nil {
				slog.ErrorContext(ctx, "hitl.insert_failed",
					"error", err, "event_id", ev.EventId)
			}
			return nil
		}
		// Fall through: forward (Claude Code inline HITL, a non-actionable
		// event, or an out-of-scope code).
	case pb.Mode_MODE_BLOCK:
		// Forward every verdict; SDK is the policy enforcement point.
	default:
		slog.WarnContext(ctx, "ws.unknown_mode_dropping_verdict",
			"mode", snap.GetMode().String(), "event_id", ev.EventId)
		return nil
	}

	out := &pb.ServerFrame{
		Frame: &pb.ServerFrame_Verdict{
			Verdict: &pb.Verdict{
				EventId:   ev.EventId,
				SessionId: sess.sessionID,
				MadCode:   madCode,
				Status:    verdictStatusProto(verdictStatus),
				Policy:    snap,
			},
		},
	}
	if !hub.Publish(sess.sessionID, out) {
		slog.WarnContext(ctx, "ws.publish_dropped",
			"event_id", ev.EventId, "session_id", sess.sessionID)
	}
	return nil
}

func verdictStatusProto(status string) pb.VerdictStatus {
	switch status {
	case "error":
		return pb.VerdictStatus_VERDICT_STATUS_ERROR
	case "ok":
		return pb.VerdictStatus_VERDICT_STATUS_OK
	default:
		return pb.VerdictStatus_VERDICT_STATUS_UNSPECIFIED
	}
}

func handleMcpInventory(ctx context.Context, sess *session, st *store.Store, inv *pb.McpInventory) error {
	if inv == nil {
		return nil
	}
	servers := make([]store.McpServer, 0, len(inv.Servers))
	for _, srv := range inv.Servers {
		transport := srv.Transport
		if transport == "" {
			transport = "unknown"
		}
		servers = append(servers, store.McpServer{
			SessionID: sess.sessionID,
			Name:      srv.Name,
			Transport: transport,
			Endpoint:  srv.Endpoint,
		})
	}
	return st.ReplaceMcpServersForSession(ctx, sess.sessionID, servers)
}
