// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"bytes"
	"crypto/sha256"
	"encoding/hex"
	"encoding/json"
	"fmt"
	"io"
	"net/http"
	"net/url"
	"os"
	"os/exec"
	"path/filepath"
	"strings"
	"sync"
)

// Meta records what a report measured, so any result can be traced back
// to the exact judge, prompt, data and code. It never holds the API key.
type Meta struct {
	Tool        string  `json:"tool"`
	StartedAt   string  `json:"started_at"`
	DurationSec float64 `json:"duration_sec"`
	Model       string  `json:"model"`
	Endpoint    string  `json:"endpoint"`
	Runs        int     `json:"runs"`
	Concurrency int     `json:"concurrency"`
	Retries     int     `json:"retries"`
	Backoff     string  `json:"backoff"`
	// OmitSamplingParams records ADRIAN_LLM_OMIT_SAMPLING_PARAMS: whether
	// temperature and stop were left out of the judge's requests.
	OmitSamplingParams bool `json:"omit_sampling_params"`
	// TimeoutSec is how long one judge call could take before it failed.
	TimeoutSec float64 `json:"timeout_sec"`
	// Interrupted is set when the run was stopped early (Ctrl+C). The
	// cases it never judged are recorded as errors, so its scores are
	// not comparable with a full run.
	Interrupted bool `json:"interrupted,omitempty"`
	// Prices are the USD per million tokens used for the cost, if given.
	Prices       *Prices `json:"prices_usd_per_million,omitempty"`
	CasesFile    string  `json:"cases_file"`
	CasesSHA256  string  `json:"cases_sha256"`
	CaseCount    int     `json:"case_count"`
	PromptSHA256 string  `json:"prompt_sha256"`
	// ProfilesFile and ProfilesSHA256 pin the agent profiles a run was
	// judged against. Without them an edit to a profile changes what the
	// profile cases are measured against while the report still looks
	// like the same setup, and -compare blames the judge for a change
	// made to the data. Empty when no profiles file was read.
	ProfilesFile   string `json:"profiles_file,omitempty"`
	ProfilesSHA256 string `json:"profiles_sha256,omitempty"`
	GitCommit      string `json:"git_commit"`
	GitDirty       bool   `json:"git_dirty"`
}

// cleanEndpoint keeps scheme, host and path, dropping any credentials or
// query string that might carry a secret.
func cleanEndpoint(raw string) string {
	u, err := url.Parse(raw)
	if err != nil || u.Host == "" {
		return "unparseable"
	}
	return u.Scheme + "://" + u.Host + u.Path
}

func fileSHA256(paths ...string) (string, error) {
	h := sha256.New()
	for _, p := range paths {
		data, err := os.ReadFile(p)
		if err != nil {
			return "", err
		}
		h.Write(data)
	}
	return hex.EncodeToString(h.Sum(nil)), nil
}

// promptSHA256 fingerprints the judge prompt files (system prompt and
// few-shot example), looking in dir, then in the usual places relative
// to the backend or repo root.
func promptSHA256(dir string) string {
	dirs := []string{dir, "internal/engine", "backend/internal/engine"}
	for _, d := range dirs {
		if d == "" {
			continue
		}
		sum, err := fileSHA256(filepath.Join(d, "system_prompt.md"), filepath.Join(d, "few_shot.md"))
		if err == nil {
			return sum
		}
	}
	return "unknown (prompt files not found; set -prompt-dir)"
}

// gitState returns the short commit and whether the tree has uncommitted
// changes. Outside a git checkout it returns "unknown".
func gitState() (string, bool) {
	out, err := exec.Command("git", "rev-parse", "--short", "HEAD").Output()
	if err != nil {
		return "unknown", false
	}
	status, _ := exec.Command("git", "status", "--porcelain").Output()
	return strings.TrimSpace(string(out)), len(bytes.TrimSpace(status)) > 0
}

// Usage is the token use reported by the model API.
type Usage struct {
	Calls           int64 `json:"calls"`
	InputTokens     int64 `json:"input_tokens"`
	CachedTokens    int64 `json:"cached_input_tokens"`
	OutputTokens    int64 `json:"output_tokens"`
	ReasoningTokens int64 `json:"reasoning_tokens"`
	// FailedCalls got no complete reply (a timeout or a network error),
	// so their tokens are missing from the counts above.
	FailedCalls int64   `json:"failed_calls,omitempty"`
	CostUSD     float64 `json:"cost_usd,omitempty"`
}

// Sub returns u minus earlier, for the usage of one run.
func (u Usage) Sub(earlier Usage) Usage {
	return Usage{
		Calls:           u.Calls - earlier.Calls,
		InputTokens:     u.InputTokens - earlier.InputTokens,
		CachedTokens:    u.CachedTokens - earlier.CachedTokens,
		OutputTokens:    u.OutputTokens - earlier.OutputTokens,
		ReasoningTokens: u.ReasoningTokens - earlier.ReasoningTokens,
		FailedCalls:     u.FailedCalls - earlier.FailedCalls,
	}
}

// Prices are USD per million tokens. Zero means unknown.
type Prices struct {
	Input       float64 `json:"input"`
	CachedInput float64 `json:"cached_input"`
	Output      float64 `json:"output"`
}

// Known reports whether any price was given.
func (p Prices) Known() bool { return p.Input != 0 || p.Output != 0 || p.CachedInput != 0 }

// envTrue reads a true/false environment variable the same way the
// judge client does: "1", "true" or "yes", in any case.
func envTrue(name string) bool {
	v := strings.ToLower(strings.TrimSpace(os.Getenv(name)))
	return v == "1" || v == "true" || v == "yes"
}

// WithCost fills CostUSD when prices are known. Cached input tokens are
// part of the input count; they use the cached price when one is given.
// Reasoning tokens are already included in output tokens.
func (u Usage) WithCost(p Prices) Usage {
	// Any price given is enough: a run priced by -price-cached alone
	// still has a cost for its cached tokens.
	if !p.Known() {
		return u
	}
	cachedPrice := p.CachedInput
	if cachedPrice == 0 {
		cachedPrice = p.Input
	}
	fresh := float64(u.InputTokens - u.CachedTokens)
	u.CostUSD = (fresh*p.Input + float64(u.CachedTokens)*cachedPrice + float64(u.OutputTokens)*p.Output) / 1e6
	return u
}

func (u Usage) String() string {
	s := fmt.Sprintf("%d calls, input %d tokens (cached %d), output %d tokens (reasoning %d)",
		u.Calls, u.InputTokens, u.CachedTokens, u.OutputTokens, u.ReasoningTokens)
	if u.CostUSD > 0 {
		s += fmt.Sprintf(", cost $%.4f", u.CostUSD)
	}
	if u.FailedCalls > 0 {
		s += fmt.Sprintf("; %d call(s) got no reply (timeout or network error) and may still be billed, so the cost is a lower bound", u.FailedCalls)
	}
	return s
}

// usageMeter wraps an HTTP transport and adds up the token usage in each
// chat-completions response. The response body is passed on unchanged.
type usageMeter struct {
	base  http.RoundTripper
	mu    sync.Mutex
	total Usage
}

func (m *usageMeter) RoundTrip(req *http.Request) (*http.Response, error) {
	resp, err := m.base.RoundTrip(req)
	if err != nil {
		m.failed()
		return resp, err
	}
	if resp.Body == nil {
		return resp, nil
	}
	body, readErr := io.ReadAll(resp.Body)
	resp.Body.Close()
	if readErr != nil {
		// The meter reads the reply before the judge client does, so a
		// connection that drops mid-reply fails here first. Swallowing
		// that and passing the truncated body on turned a retryable
		// network error into "unmarshal: unexpected end of JSON input",
		// which isTransient does not match: the case was recorded as a
		// judge error instead of being retried, and on a block case the
		// gate counted it as a missed block. A RoundTripper that errors
		// must return a nil response, so the client sees the network
		// error it would have seen without the meter.
		m.failed()
		return nil, readErr
	}
	resp.Body = io.NopCloser(bytes.NewReader(body))
	var parsed struct {
		Usage *struct {
			PromptTokens        int64 `json:"prompt_tokens"`
			CompletionTokens    int64 `json:"completion_tokens"`
			PromptTokensDetails struct {
				CachedTokens int64 `json:"cached_tokens"`
			} `json:"prompt_tokens_details"`
			CompletionTokensDetails struct {
				ReasoningTokens int64 `json:"reasoning_tokens"`
			} `json:"completion_tokens_details"`
		} `json:"usage"`
	}
	if json.Unmarshal(body, &parsed) == nil && parsed.Usage != nil {
		m.mu.Lock()
		m.total.Calls++
		m.total.InputTokens += parsed.Usage.PromptTokens
		m.total.CachedTokens += parsed.Usage.PromptTokensDetails.CachedTokens
		m.total.OutputTokens += parsed.Usage.CompletionTokens
		m.total.ReasoningTokens += parsed.Usage.CompletionTokensDetails.ReasoningTokens
		m.mu.Unlock()
	}
	return resp, nil
}

// failed counts a call that got no complete reply: a timeout or a
// network error. Its tokens are unknown, yet the provider may still
// bill them, so a run with failed calls has a cost that is a lower
// bound.
func (m *usageMeter) failed() {
	m.mu.Lock()
	m.total.FailedCalls++
	m.mu.Unlock()
}

// Snapshot returns the usage counted so far.
func (m *usageMeter) Snapshot() Usage {
	m.mu.Lock()
	defer m.mu.Unlock()
	return m.total
}

// Print writes a short description of what was measured.
func (m Meta) Print(w io.Writer) {
	dirty := ""
	if m.GitDirty {
		dirty = " (uncommitted changes)"
	}
	fmt.Fprintf(w, "Judge:                 %s at %s\n", m.Model, m.Endpoint)
	fmt.Fprintf(w, "Prompt fingerprint:    %s\n", short(m.PromptSHA256))
	fmt.Fprintf(w, "Cases:                 %d from %s (%s)\n", m.CaseCount, m.CasesFile, short(m.CasesSHA256))
	fmt.Fprintf(w, "Code:                  %s%s, %s, %.0fs\n", m.GitCommit, dirty, m.StartedAt, m.DurationSec)
	if m.ProfilesFile != "" {
		fmt.Fprintf(w, "Profiles:              %s (%s)\n", m.ProfilesFile, short(m.ProfilesSHA256))
	}
	fmt.Fprintf(w, "Settings:              runs %d, concurrency %d, retries %d, backoff %s, timeout %.0fs, omit sampling params %v\n",
		m.Runs, m.Concurrency, m.Retries, m.Backoff, m.TimeoutSec, m.OmitSamplingParams)
	if m.Interrupted {
		fmt.Fprintln(w, "INTERRUPTED:           stopped early; cases not judged count as errors, so do not compare this report with a full run")
	}
	if m.Prices != nil {
		fmt.Fprintf(w, "Prices (USD/M tokens): input %.2f, cached input %.2f, output %.2f\n", m.Prices.Input, m.Prices.CachedInput, m.Prices.Output)
	}
}

func short(sum string) string {
	if len(sum) == 64 {
		return sum[:12]
	}
	return sum
}
