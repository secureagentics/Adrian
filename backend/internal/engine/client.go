// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package engine

import (
	"bytes"
	"context"
	"encoding/json"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"time"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
	"github.com/secureagentics/Adrian/backend/internal/store"
)

const (
	maxCompletionTokens = 425
	classifyTimeout     = 30 * time.Second
	reasoningEffort     = "low"
	// pingTimeout caps the readiness probe so /readyz responds even
	// when the upstream is half-down. Independent of classifyTimeout
	// because a healthy classify can take seconds while a healthy
	// reachability probe is sub-100 ms.
	pingTimeout = 2 * time.Second
)

// HTTPClient classifies paired events by POSTing to ADRIAN_LLM_URL.
// Classifier failures (transport, non-2xx HTTP, malformed body,
// empty choices, or no parseable M-code) are returned as errors. The
// WS ingest layer records those as status=ERROR verdicts and applies
// the active execution policy.
//
// The classifier owns the SlidingWindow: every call acquires the
// per-(session, invocation, agent_id) lock, reads history into the
// prompt, classifies, then pushes the new turn back into the window
// before releasing. Different keys classify in parallel; same-key
// events serialise so their prompts always carry consistent history.
type HTTPClient struct {
	url    string
	apiKey string
	model  string
	http   *http.Client
	window *SlidingWindow
	store  *store.Store
}

// NewHTTPClient builds a Classifier that POSTs to the given URL. The
// URL is treated as the complete endpoint (no path appending); points
// at the bundled Llama.cpp container's /v1/chat/completions.
//
// window may be nil; in that case every call classifies history-less
// (handy for tests). Production wiring always supplies one.
//
// st may be nil; in that case the agent profile is never looked up
// and the system prompt always renders against the generic remit.
// Production wiring always supplies a store.
func NewHTTPClient(url, apiKey, model string, window *SlidingWindow, st *store.Store) Classifier {
	return &HTTPClient{
		url:    url,
		apiKey: apiKey,
		model:  model,
		http:   &http.Client{Timeout: classifyTimeout},
		window: window,
		store:  st,
	}
}

// requestBody is the JSON we POST. Field naming follows the Chat
// Completions schema Llama.cpp's server speaks.
//
// reasoning_effort caps the model's chain-of-thought budget on
// reasoning models. Without it, complex traces can burn the full
// max_completion_tokens budget on the <reasoning> block alone and
// emit no M-code, surfacing as a classify_failed with an empty
// content. "low" leaves enough room for a useful reasoning pass
// while ensuring the answer lands. Llama.cpp ignores the field.
type requestBody struct {
	Model               string        `json:"model"`
	Messages            []chatMessage `json:"messages"`
	MaxCompletionTokens int           `json:"max_completion_tokens"`
	Temperature         float64       `json:"temperature"`
	ReasoningEffort     string        `json:"reasoning_effort,omitempty"`
	Stop                []string      `json:"stop"`
}

// responseBody captures the subset we need from the response.
type responseBody struct {
	Choices []struct {
		Message chatMessage `json:"message"`
	} `json:"choices"`
	Usage struct {
		TotalTokens int `json:"total_tokens"`
	} `json:"usage"`
}

func (c *HTTPClient) Classify(ctx context.Context, ev *pb.PairedEvent, agentProfileID string) (*Verdict, error) {
	profile := c.lookupProfile(ctx, agentProfileID)
	key := keyFromEvent(ev)
	if c.window == nil || !key.complete() {
		// No window configured (test wiring) or the event is missing
		// identity fields, classify history-less, with a fresh guid
		// each call. No turn is written back to the window.
		return c.classifyOnce(ctx, ev, nil, freshGuid(), profile)
	}

	h := c.window.Acquire(key)
	defer h.Release()

	history := h.History()
	guid := h.Guid()
	verdict, err := c.classifyOnce(ctx, ev, history, guid, profile)
	if err != nil {
		// Failed turns aren't pushed to the window: the model should
		// see consistent prior turns next time, not a record of a
		// classification that never happened.
		return nil, err
	}
	h.Push(ev, verdict.MADCode)
	return verdict, nil
}

// lookupProfile returns the agent profile for id, or nil for empty id
// or any error. Errors are logged and swallowed so a single transient
// store hiccup never fails-closed an SDK classify call, the policy
// path simply renders against the generic remit.
func (c *HTTPClient) lookupProfile(ctx context.Context, id string) *store.AgentProfile {
	if id == "" || c.store == nil {
		return nil
	}
	p, err := c.store.GetAgentProfile(ctx, id)
	if err != nil {
		if !errors.Is(err, store.ErrNotFound) {
			slog.WarnContext(ctx, "engine.profile_lookup_failed",
				"agent_profile_id", id, "error", err)
		}
		return nil
	}
	return p
}

// classifyOnce renders the trace, builds the message array (with the
// optional history prepended), POSTs, and parses. Returns (nil, error)
// on any failure; the WS handler is responsible for persisting the
// status=ERROR verdict and applying the active execution policy.
func (c *HTTPClient) classifyOnce(ctx context.Context, ev *pb.PairedEvent, history []HistoryItem, guid string, profile *store.AgentProfile) (*Verdict, error) {
	start := time.Now()
	trace := extractTrace(ev, guid)
	body := requestBody{
		Model:               c.model,
		Messages:            buildMessages(ctx, trace, history, profile, guid),
		MaxCompletionTokens: maxCompletionTokens,
		Temperature:         0.0,
		ReasoningEffort:     reasoningEffort,
		Stop:                []string{"}"},
	}

	raw, err := c.post(ctx, body)
	if err != nil {
		return nil, fmt.Errorf("post: %w", err)
	}

	var parsed responseBody
	if err := json.Unmarshal(raw, &parsed); err != nil {
		return nil, fmt.Errorf("unmarshal: %w", err)
	}
	if len(parsed.Choices) == 0 {
		return nil, errors.New("no choices in response")
	}

	rawContent := parsed.Choices[0].Message.Content
	stripped := stripReasoning(rawContent)
	code := parseMADCode(stripped)
	if code == "" {
		return nil, fmt.Errorf("no MAD code in response: %q", truncate(stripped, 200))
	}

	classification := madCodeToClassification(code)
	latency := time.Since(start).Milliseconds()
	slog.InfoContext(ctx, "engine.classify",
		"event_id", ev.EventId,
		"mad_code", code,
		"classification", classification,
		"latency_ms", latency,
		"history_len", len(history),
	)
	return &Verdict{
		MADCode:        code,
		Classification: classification,
		Reasoning:      rawContent,
		LatencyMS:      latency,
	}, nil
}

func (c *HTTPClient) post(ctx context.Context, body requestBody) ([]byte, error) {
	buf, err := json.Marshal(body)
	if err != nil {
		return nil, err
	}
	req, err := http.NewRequestWithContext(ctx, http.MethodPost, c.url, bytes.NewReader(buf))
	if err != nil {
		return nil, err
	}
	req.Header.Set("Content-Type", "application/json")
	req.Header.Set("Authorization", "Bearer "+c.apiKey)

	resp, err := c.http.Do(req)
	if err != nil {
		return nil, err
	}
	defer resp.Body.Close()

	respBody, err := io.ReadAll(resp.Body)
	if err != nil {
		return nil, err
	}
	if resp.StatusCode < 200 || resp.StatusCode >= 300 {
		return nil, fmt.Errorf("status %d: %s", resp.StatusCode, truncate(string(respBody), 200))
	}
	return respBody, nil
}

// Ping reaches the configured classifier URL with a short timeout to
// confirm the upstream answers TCP + TLS + HTTP. Treats any HTTP
// status (including 4xx like 405 Method Not Allowed for our POST-only
// endpoint) as "reachable", the goal is to detect dial / DNS /
// timeout failures, not to validate the model's behaviour. No tokens
// are consumed because we never POST a chat-completions body.
func (c *HTTPClient) Ping(ctx context.Context) error {
	probeCtx, cancel := context.WithTimeout(ctx, pingTimeout)
	defer cancel()
	req, err := http.NewRequestWithContext(probeCtx, http.MethodGet, c.url, nil)
	if err != nil {
		return err
	}
	resp, err := c.http.Do(req)
	if err != nil {
		return err
	}
	defer resp.Body.Close()
	// Drain a small amount so the connection can be reused.
	_, _ = io.Copy(io.Discard, resp.Body)
	return nil
}

func truncate(s string, n int) string {
	if len(s) <= n {
		return s
	}
	return s[:n] + "..."
}
