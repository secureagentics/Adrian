// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	"time"

	"github.com/gorilla/websocket"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

// schemaVersion is the wire schema_version the SDK is required to
// declare in its SessionLogin frame. Locked to match the SDK's
// `SCHEMA_VERSION = 2` constant.
const schemaVersion = 2

// Close-code constants. Standard 1000-series WebSocket close codes
// plus our application-specific 4xxx codes.
const (
	closeProtocolError     = 1002
	closePolicyViolation   = 1008
	closeInternalServerErr = 1011
	closeQuotaExhausted    = 4003
)

// PolicySnapshot converts the singleton policy row to the wire
// PolicySnapshot the SDK reads. Exported so the REST review handler
// can build HITL-resolution Verdict frames carrying the same shape.
func PolicySnapshot(p *store.Policy) *pb.PolicySnapshot {
	return &pb.PolicySnapshot{
		Mode:                        modeFromString(p.Mode),
		PolicyM0:                    p.PolicyM0,
		PolicyM2:                    p.PolicyM2,
		PolicyM3:                    p.PolicyM3,
		PolicyM4:                    p.PolicyM4,
		FailClosedOnClassifierError: p.FailClosedOnClassifierError,
	}
}

func modeFromString(s string) pb.Mode {
	switch s {
	case "alert":
		return pb.Mode_MODE_ALERT
	case "hitl":
		return pb.Mode_MODE_HITL
	case "block":
		return pb.Mode_MODE_BLOCK
	default:
		return pb.Mode_MODE_UNSPECIFIED
	}
}

// closeWith sends a close frame with the given code and reason. Best
// effort: errors are swallowed because the connection is going away
// regardless.
func closeWith(conn *websocket.Conn, code int, reason string) {
	msg := websocket.FormatCloseMessage(code, reason)
	_ = conn.WriteControl(websocket.CloseMessage, msg, time.Now().Add(2*time.Second))
}
