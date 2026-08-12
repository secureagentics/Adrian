// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package ws

import (
	"encoding/json"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

// pairedEventToJSON re-encodes a pb.PairedEvent as the JSON shape we
// persist in events.payload. The dashboard and the engine read JSON,
// not the binary proto, so the conversion happens at ingest.
//
// Field names mirror the proto (snake_case) so a round-trip back into
// a fresh proto message is possible if needed later.
func pairedEventToJSON(ev *pb.PairedEvent) (string, error) {
	view := map[string]any{
		"event_id":      ev.EventId,
		"invocation_id": ev.InvocationId,
		"session_id":    ev.SessionId,
		"run_id":        ev.RunId,
		"parent_run_id": ev.ParentRunId,
		"timestamp":     ev.Timestamp,
		"pair_type":     pairTypeName(ev.PairType),
	}
	if ev.Agent != nil {
		view["agent"] = agentContextToJSON(ev.Agent)
	}
	if ev.Parent != nil {
		view["parent"] = agentContextToJSON(ev.Parent)
	}
	if llm := ev.GetLlm(); llm != nil {
		view["llm"] = map[string]any{
			"model":      llm.Model,
			"output":     llm.Output,
			"reasoning":  llm.Reasoning,
			"messages":   chatMessagesToJSON(llm.Messages),
			"tool_calls": toolCallsToJSON(llm.ToolCalls),
			"usage":      tokenUsageToJSON(llm.Usage),
		}
	}
	if tool := ev.GetTool(); tool != nil {
		view["tool"] = map[string]any{
			"tool_name":    tool.ToolName,
			"tool_call_id": tool.ToolCallId,
			"input":        tool.Input,
			"output":       tool.Output,
		}
	}
	if len(ev.MetadataJson) > 0 {
		view["metadata_json"] = string(ev.MetadataJson)
	}
	buf, err := json.Marshal(view)
	if err != nil {
		return "", err
	}
	return string(buf), nil
}

func pairTypeName(t pb.PairType) string {
	switch t {
	case pb.PairType_PAIR_TYPE_LLM:
		return "llm"
	case pb.PairType_PAIR_TYPE_TOOL:
		return "tool"
	default:
		return "unknown"
	}
}

func agentContextToJSON(a *pb.AgentContext) map[string]any {
	return map[string]any{
		"agent_id":         a.AgentId,
		"system_prompt":    a.SystemPrompt,
		"user_instruction": a.UserInstruction,
	}
}

func chatMessagesToJSON(msgs []*pb.ChatMessage) []map[string]string {
	out := make([]map[string]string, 0, len(msgs))
	for _, m := range msgs {
		out = append(out, map[string]string{"role": m.Role, "content": m.Content})
	}
	return out
}

func toolCallsToJSON(calls []*pb.ToolCall) []map[string]string {
	out := make([]map[string]string, 0, len(calls))
	for _, c := range calls {
		out = append(out, map[string]string{"name": c.Name, "args": c.Args, "id": c.Id})
	}
	return out
}

func tokenUsageToJSON(u *pb.TokenUsage) map[string]int32 {
	if u == nil {
		return nil
	}
	return map[string]int32{
		"prompt_tokens":     u.PromptTokens,
		"completion_tokens": u.CompletionTokens,
		"total_tokens":      u.TotalTokens,
	}
}

// totalTokens returns the per-event token total. Currently only LLM
// pairs carry a TokenUsage; tool pairs return 0.
func totalTokens(ev *pb.PairedEvent) int32 {
	if llm := ev.GetLlm(); llm != nil && llm.Usage != nil {
		return llm.Usage.TotalTokens
	}
	return 0
}

// newEventRow builds the store.Event payload to persist.
func newEventRow(sess *session, ev *pb.PairedEvent, payloadJSON string) *store.Event {
	agentID := ""
	if ev.Agent != nil {
		agentID = ev.Agent.AgentId
	}
	return &store.Event{
		ID:             ev.EventId,
		SessionID:      sess.sessionID,
		AgentID:        agentID,
		AgentProfileID: sess.agentProfileID(),
		EventType:      pairTypeName(ev.PairType),
		RunID:          ev.RunId,
		PayloadJSON:    payloadJSON,
		TokensUsed:     totalTokens(ev),
	}
}
