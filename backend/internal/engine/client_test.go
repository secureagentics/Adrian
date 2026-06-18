// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package engine

import (
	"context"
	"encoding/json"
	"io"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// -----------------------------------------------------------------
// parseMADCode
// -----------------------------------------------------------------

func TestParseMADCode(t *testing.T) {
	cases := []struct {
		in, want string
	}{
		{"M0", "M0"},
		{"M2", "M2"},
		{"M2.a", "M2.a"},
		{"M3.b", "M3.b"},
		{"M4.e", "M4.e"},
		{"  M3.f\n", "M3.f"},
		{"Answer: M2.c", "M2.c"},
		{"prefix M0 suffix", "M0"},
		{"M1", ""},     // not in taxonomy
		{"M1.a", ""},   // not in taxonomy
		{"M5", ""},     // not in taxonomy
		{"M3.A", "M3"}, // malformed suffix -> falls back to M3 base
		{"M3_a", "M3"}, // legacy underscore -> falls back to M3 base
		{"", ""},       // empty
		{"no code here", ""},
	}
	for _, c := range cases {
		got := parseMADCode(c.in)
		if got != c.want {
			t.Errorf("parseMADCode(%q) = %q, want %q", c.in, got, c.want)
		}
	}
}

// -----------------------------------------------------------------
// madCodeToClassification
// -----------------------------------------------------------------

func TestStripReasoning(t *testing.T) {
	cases := map[string]string{
		"M3.b":                                "M3.b",
		"<reasoning>thinking</reasoning>M3.b": "M3.b",
		"prefix <reasoning>a</reasoning>middle<reasoning>b</reasoning>M0 suffix": "prefix middleM0 suffix",
		"<reasoning>open without close":                                          "",
		"<reasoning>first</reasoning><reasoning>second</reasoning>M2.a":          "M2.a",
	}
	for in, want := range cases {
		if got := stripReasoning(in); got != want {
			t.Errorf("stripReasoning(%q) = %q, want %q", in, got, want)
		}
	}
}

// Reasoning blocks must be removed before parseMADCode so a stray
// M-code reference inside the model's thinking does not get returned
// as the answer.
func TestStripReasoningThenParse(t *testing.T) {
	in := `<reasoning>The user mentioned M3 in their query, but that's just context.</reasoning>M0`
	stripped := stripReasoning(in)
	got := parseMADCode(stripped)
	if got != "M0" {
		t.Errorf("after strip, parseMADCode = %q, want M0 (had reasoning citing M3)", got)
	}
}

func TestMADCodeToClassification(t *testing.T) {
	cases := map[string]string{
		"M0":   "benign",
		"M2":   "notify",
		"M2.a": "notify",
		"M2.g": "notify",
		"M3":   "block",
		"M3.b": "block",
		"M4":   "block",
		"M4.e": "block",
		"":     "error",
	}
	for code, want := range cases {
		if got := madCodeToClassification(code); got != want {
			t.Errorf("madCodeToClassification(%q) = %q, want %q", code, got, want)
		}
	}
}

// -----------------------------------------------------------------
// extractTrace
// -----------------------------------------------------------------

func TestExtractTraceTool(t *testing.T) {
	ev := &pb.PairedEvent{
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Agent: &pb.AgentContext{
			AgentId:         "shopper",
			SystemPrompt:    "You are a helpful shopping assistant.",
			UserInstruction: "Find Q3 reports.",
		},
		Data: &pb.PairedEvent_Tool{
			Tool: &pb.ToolPairData{
				ToolName:   "search_documents",
				ToolCallId: "tc-1",
				Input:      `{"q":"Q3"}`,
				Output:     "Found 3 results.",
			},
		},
	}
	const guid = "test-guid"
	got := extractTrace(ev, guid)
	// Untrusted interpolations are wrapped under the per-conversation
	// guid; trusted scaffolding (labels, separators, "Tool Invocation:")
	// stays raw.
	wantWrapped := []string{
		`<adrian-untrusted id="test-guid">You are a helpful shopping assistant.</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">Find Q3 reports.</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">search_documents</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">{"q":"Q3"}</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">Found 3 results.</adrian-untrusted id="test-guid">`,
	}
	for _, w := range wantWrapped {
		if !strings.Contains(got, w) {
			t.Errorf("extractTrace missing wrapped fragment %q in:\n%s", w, got)
		}
	}
	for _, w := range []string{"Tool Invocation:", "Tool: ", "Input: ", "Output: "} {
		if !strings.Contains(got, w) {
			t.Errorf("extractTrace missing scaffold %q in:\n%s", w, got)
		}
	}
	if strings.Contains(got, "Parent system prompt:") {
		t.Errorf("expected no parent block; got:\n%s", got)
	}
}

func TestExtractTraceLLMWithParent(t *testing.T) {
	ev := &pb.PairedEvent{
		PairType: pb.PairType_PAIR_TYPE_LLM,
		Agent: &pb.AgentContext{
			AgentId:         "specialist",
			SystemPrompt:    "Specialist role.",
			UserInstruction: "Refine.",
		},
		Parent: &pb.AgentContext{
			AgentId:         "triage",
			SystemPrompt:    "Triage role.",
			UserInstruction: "Initial intake.",
		},
		Data: &pb.PairedEvent_Llm{
			Llm: &pb.LlmPairData{
				Model:  "gpt-4o",
				Output: "I will run the search.",
				ToolCalls: []*pb.ToolCall{
					{Name: "search", Args: `{"q":"x"}`, Id: "tc-2"},
				},
			},
		},
	}
	const guid = "test-guid"
	got := extractTrace(ev, guid)
	wantWrapped := []string{
		`<adrian-untrusted id="test-guid">Triage role.</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">Initial intake.</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">I will run the search.</adrian-untrusted id="test-guid">`,
		`<adrian-untrusted id="test-guid">search({"q":"x"})</adrian-untrusted id="test-guid">`,
	}
	for _, w := range wantWrapped {
		if !strings.Contains(got, w) {
			t.Errorf("extractTrace missing wrapped fragment %q in:\n%s", w, got)
		}
	}
	for _, w := range []string{"Parent system prompt: ", "Chain of Thought: ", "Tool Calls:"} {
		if !strings.Contains(got, w) {
			t.Errorf("extractTrace missing scaffold %q in:\n%s", w, got)
		}
	}
}

// -----------------------------------------------------------------
// HTTPClient.Classify
// -----------------------------------------------------------------

func TestHTTPClientClassifyHappy(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		if got := r.Header.Get("Authorization"); got != "Bearer test-key" {
			t.Errorf("auth header = %q, want %q", got, "Bearer test-key")
		}
		body, _ := io.ReadAll(r.Body)
		var req requestBody
		if err := json.Unmarshal(body, &req); err != nil {
			t.Fatalf("unmarshal request: %v", err)
		}
		if req.Model != "test-model" {
			t.Errorf("body.model = %q, want test-model", req.Model)
		}
		if req.MaxCompletionTokens != 425 {
			t.Errorf("body.max_completion_tokens = %d, want 425", req.MaxCompletionTokens)
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"role":"assistant","content":"M3.b"}}]}`))
	}))
	defer srv.Close()

	c := NewHTTPClient(srv.URL, "test-key", "test-model", nil, nil)
	v, err := c.Classify(context.Background(), &pb.PairedEvent{
		EventId:  "ev-1",
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Data: &pb.PairedEvent_Tool{
			Tool: &pb.ToolPairData{ToolName: "noop"},
		},
	}, "")
	if err != nil {
		t.Fatalf("Classify: %v", err)
	}
	if v.MADCode != "M3.b" {
		t.Errorf("mad_code = %q, want M3.b", v.MADCode)
	}
	if v.Classification != "block" {
		t.Errorf("classification = %q, want block", v.Classification)
	}
}

// TestHTTPClientClassifyErrorsOn5xx asserts that an upstream HTTP
// error (e.g. 500) is returned to the WS ingest layer so it can
// persist an ERROR verdict and apply policy.
func TestHTTPClientClassifyErrorsOn5xx(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		http.Error(w, "boom", http.StatusInternalServerError)
	}))
	defer srv.Close()

	c := NewHTTPClient(srv.URL, "test-key", "test-model", nil, nil)
	v, err := c.Classify(context.Background(), &pb.PairedEvent{
		EventId:  "ev-5xx",
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Data: &pb.PairedEvent_Tool{
			Tool: &pb.ToolPairData{ToolName: "noop"},
		},
	}, "")
	if err == nil {
		t.Fatal("Classify on 5xx unexpectedly succeeded")
	}
	if v != nil {
		t.Fatalf("verdict = %+v, want nil on classifier error", v)
	}
	if !strings.Contains(err.Error(), "post:") || !strings.Contains(err.Error(), "status 500") {
		t.Errorf("error should reference upstream status; got %v", err)
	}
}

// TestHTTPClientClassifyErrorsOnConnRefused asserts the transport
// failure path (server unreachable / connection refused) returns an
// error rather than a synthetic benign verdict.
func TestHTTPClientClassifyErrorsOnConnRefused(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		// Will not be hit; we close the server before calling Classify.
		w.WriteHeader(http.StatusOK)
	}))
	url := srv.URL
	srv.Close()

	c := NewHTTPClient(url, "test-key", "test-model", nil, nil)
	v, err := c.Classify(context.Background(), &pb.PairedEvent{
		EventId:  "ev-refused",
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Data: &pb.PairedEvent_Tool{
			Tool: &pb.ToolPairData{ToolName: "noop"},
		},
	}, "")
	if err == nil {
		t.Fatal("Classify on connection-refused unexpectedly succeeded")
	}
	if v != nil {
		t.Fatalf("verdict = %+v, want nil on classifier error", v)
	}
	if !strings.Contains(err.Error(), "post:") {
		t.Errorf("error should identify post failure; got %v", err)
	}
}

// TestHTTPClientClassifyErrorsOnUnparseable asserts the
// 2xx-with-garbled-body path: upstream answered, body has no
// recognisable M-code, so engine returns an error.
func TestHTTPClientClassifyErrorsOnUnparseable(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"sorry, no idea"}}]}`))
	}))
	defer srv.Close()

	c := NewHTTPClient(srv.URL, "test-key", "test-model", nil, nil)
	v, err := c.Classify(context.Background(), &pb.PairedEvent{
		EventId:  "ev-noparse",
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Data:     &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{ToolName: "noop"}},
	}, "")
	if err == nil {
		t.Fatal("Classify on unparseable body unexpectedly succeeded")
	}
	if v != nil {
		t.Fatalf("verdict = %+v, want nil on classifier error", v)
	}
	if !strings.Contains(err.Error(), "no MAD code") {
		t.Errorf("error should explain the parse miss; got %v", err)
	}
}

func TestHTTPClientClassifyErrorsOnMalformedJSON(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`not-json`))
	}))
	defer srv.Close()

	c := NewHTTPClient(srv.URL, "test-key", "test-model", nil, nil)
	v, err := c.Classify(context.Background(), &pb.PairedEvent{
		EventId:  "ev-malformed",
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Data:     &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{ToolName: "noop"}},
	}, "")
	if err == nil {
		t.Fatal("Classify on malformed JSON unexpectedly succeeded")
	}
	if v != nil {
		t.Fatalf("verdict = %+v, want nil on classifier error", v)
	}
	if !strings.Contains(err.Error(), "unmarshal:") {
		t.Errorf("error should explain malformed JSON; got %v", err)
	}
}

// TestHTTPClientClassifyErrorsOnEmptyChoices is the second
// unparseable-response branch: 2xx + valid JSON envelope, but the
// choices array is empty.
func TestHTTPClientClassifyErrorsOnEmptyChoices(t *testing.T) {
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		_, _ = w.Write([]byte(`{"choices":[]}`))
	}))
	defer srv.Close()

	c := NewHTTPClient(srv.URL, "test-key", "test-model", nil, nil)
	v, err := c.Classify(context.Background(), &pb.PairedEvent{
		EventId:  "ev-empty",
		PairType: pb.PairType_PAIR_TYPE_TOOL,
		Data:     &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{ToolName: "noop"}},
	}, "")
	if err == nil {
		t.Fatal("Classify on empty-choices unexpectedly succeeded")
	}
	if v != nil {
		t.Fatalf("verdict = %+v, want nil on classifier error", v)
	}
	if !strings.Contains(err.Error(), "no choices") {
		t.Errorf("error should explain empty choices; got %v", err)
	}
}

// TestHTTPClientWindowFeedsHistory drives two same-key Classify calls
// and asserts the second request's messages array carries the prior
// turn (user trace + assistant M-code) between the few-shot pair and
// the new user message.
func TestHTTPClientWindowFeedsHistory(t *testing.T) {
	var captured []requestBody
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req requestBody
		_ = json.Unmarshal(body, &req)
		captured = append(captured, req)
		// Return a different M-code each call so we can verify which
		// one is replayed in the history of the second call.
		madCode := "M0"
		if len(captured) == 1 {
			madCode = "M3.b"
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"` + madCode + `"}}]}`))
	}))
	defer srv.Close()

	window := NewSlidingWindow(WindowOpts{Size: 16, TTL: time.Hour})
	c := NewHTTPClient(srv.URL, "test-key", "test-model", window, nil)

	mkEvent := func(eventID, toolName string) *pb.PairedEvent {
		return &pb.PairedEvent{
			EventId:      eventID,
			SessionId:    "sess-w",
			InvocationId: "inv-w",
			PairType:     pb.PairType_PAIR_TYPE_TOOL,
			Agent:        &pb.AgentContext{AgentId: "agent-w"},
			Data:         &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{ToolName: toolName}},
		}
	}

	if _, err := c.Classify(context.Background(), mkEvent("ev-1", "first_tool"), ""); err != nil {
		t.Fatalf("first classify: %v", err)
	}
	if _, err := c.Classify(context.Background(), mkEvent("ev-2", "second_tool"), ""); err != nil {
		t.Fatalf("second classify: %v", err)
	}

	if len(captured) != 2 {
		t.Fatalf("captured %d requests, want 2", len(captured))
	}

	// First call: system + few-shot pair + 1 current user = 4.
	if got := len(captured[0].Messages); got != 4 {
		t.Errorf("first call messages = %d, want 4", got)
	}
	// Second call: system + few-shot pair + 1 history pair + 1 current = 6.
	if got := len(captured[1].Messages); got != 6 {
		t.Fatalf("second call messages = %d, want 6", got)
	}
	historyUser := captured[1].Messages[3]
	historyAssistant := captured[1].Messages[4]
	if historyUser.Role != "user" || !strings.Contains(historyUser.Content, "first_tool") {
		t.Errorf("history user message wrong: role=%q content=%q", historyUser.Role, historyUser.Content)
	}
	if historyAssistant.Role != "assistant" || historyAssistant.Content != "M3.b" {
		t.Errorf("history assistant should replay the prior M-code; got %q / %q",
			historyAssistant.Role, historyAssistant.Content)
	}
}

func TestHTTPClientWindowSkipsFailedTurns(t *testing.T) {
	var captured []requestBody
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req requestBody
		_ = json.Unmarshal(body, &req)
		captured = append(captured, req)
		if len(captured) == 1 {
			_, _ = w.Write([]byte(`{"choices":[]}`))
			return
		}
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"M0"}}]}`))
	}))
	defer srv.Close()

	window := NewSlidingWindow(WindowOpts{Size: 16, TTL: time.Hour})
	c := NewHTTPClient(srv.URL, "test-key", "test-model", window, nil)
	event := &pb.PairedEvent{
		EventId:      "ev-window-fail",
		SessionId:    "sess-window-fail",
		InvocationId: "inv-window-fail",
		PairType:     pb.PairType_PAIR_TYPE_TOOL,
		Agent:        &pb.AgentContext{AgentId: "agent-window-fail"},
		Data:         &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{ToolName: "first_tool"}},
	}

	if _, err := c.Classify(context.Background(), event, ""); err == nil {
		t.Fatal("first classify unexpectedly succeeded")
	}
	event.EventId = "ev-window-success"
	if _, err := c.Classify(context.Background(), event, ""); err != nil {
		t.Fatalf("second classify: %v", err)
	}

	if len(captured) != 2 {
		t.Fatalf("captured %d requests, want 2", len(captured))
	}
	if got := len(captured[1].Messages); got != 4 {
		t.Fatalf("second call messages = %d, want 4 (failed turn not pushed to history)", got)
	}
}

// TestHTTPClientNoWindowSkipsHistory ensures the existing zero-config
// path (window=nil) works exactly as before: every call sees no
// history regardless of any prior call.
func TestHTTPClientNoWindowSkipsHistory(t *testing.T) {
	var capturedLen int
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		body, _ := io.ReadAll(r.Body)
		var req requestBody
		_ = json.Unmarshal(body, &req)
		capturedLen = len(req.Messages)
		_, _ = w.Write([]byte(`{"choices":[{"message":{"content":"M0"}}]}`))
	}))
	defer srv.Close()

	c := NewHTTPClient(srv.URL, "test-key", "test-model", nil, nil)
	for i := 0; i < 3; i++ {
		_, _ = c.Classify(context.Background(), &pb.PairedEvent{
			EventId: "ev",
			Data:    &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{ToolName: "noop"}},
		}, "")
	}
	if capturedLen != 4 {
		t.Errorf("no-window path messages = %d, want 4 (no history accumulation)", capturedLen)
	}
}
