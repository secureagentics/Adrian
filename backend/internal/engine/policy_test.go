// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package engine

import (
	"context"
	"strings"
	"testing"

	"github.com/secureagentics/Adrian/backend/internal/store"
)

func TestWrapShape(t *testing.T) {
	got := wrap("hello world", "G123")
	want := `<adrian-untrusted id="G123">hello world</adrian-untrusted id="G123">`
	if got != want {
		t.Errorf("wrap = %q, want %q", got, want)
	}
}

func TestRenderPolicyEmptyProfile(t *testing.T) {
	got := renderPolicy(context.Background(), nil, "G")
	if strings.Contains(got, "{REMIT}") || strings.Contains(got, "{CONVENTION}") ||
		strings.Contains(got, "{OUTPUT_CODES}") ||
		strings.Contains(got, "{M0_USER_BLOCK}") || strings.Contains(got, "{M3_USER_BLOCK}") {
		t.Errorf("rendered prompt still contains a placeholder:\n%s", got)
	}
	if !strings.Contains(got, genericRemit) {
		t.Errorf("expected generic remit in output, got:\n%s", got)
	}
	// No User-defined blocks when profile is nil.
	for _, banned := range []string{"User-defined expected behaviours", "User-defined known risks"} {
		if strings.Contains(got, banned) {
			t.Errorf("nil profile should not render %q; got:\n%s", banned, got)
		}
	}
	// Output codes line should be the static set.
	if !strings.Contains(got, "Output format: Return one of M0, M2.a") {
		t.Errorf("expected static output codes line; got:\n%s", got)
	}
}

func TestRenderPolicyDisabledProfile(t *testing.T) {
	p := &store.AgentProfile{
		Enabled:   false,
		Remit:     "manage refunds only",
		M0Entries: `["talk politely"]`,
		M3Entries: `["never disclose passwords"]`,
	}
	got := renderPolicy(context.Background(), p, "G")
	if strings.Contains(got, "manage refunds only") {
		t.Errorf("disabled profile leaked remit into prompt:\n%s", got)
	}
	if strings.Contains(got, "User-defined") {
		t.Errorf("disabled profile leaked user blocks into prompt:\n%s", got)
	}
}

func TestRenderPolicyNameFallback(t *testing.T) {
	p := &store.AgentProfile{
		Enabled: true,
		Name:    "support-bot",
		Remit:   "",
	}
	got := renderPolicy(context.Background(), p, "G")
	wantRemit := `<adrian-untrusted id="G">support-bot</adrian-untrusted id="G">`
	if !strings.Contains(got, wantRemit) {
		t.Errorf("expected name to fall in for empty remit, wrapped %q; got:\n%s", wantRemit, got)
	}
	if strings.Contains(got, genericRemit) {
		t.Errorf("generic remit leaked when name fallback should have applied")
	}
}

func TestRenderPolicyWithProfile(t *testing.T) {
	p := &store.AgentProfile{
		Enabled:   true,
		Remit:     "manage refunds only",
		M0Entries: `["talk politely"]`,
		M3Entries: `["never disclose passwords"]`,
	}
	got := renderPolicy(context.Background(), p, "G")

	// Remit lands wrapped under the per-conversation guid.
	wantRemit := `<adrian-untrusted id="G">manage refunds only</adrian-untrusted id="G">`
	if !strings.Contains(got, wantRemit) {
		t.Errorf("expected wrapped remit %q; got:\n%s", wantRemit, got)
	}
	// User blocks render with codes starting at the next-letter past
	// what the static taxonomy uses today.
	wantM0 := "User-defined expected behaviours"
	wantM3 := "User-defined known risks"
	if !strings.Contains(got, wantM0) || !strings.Contains(got, wantM3) {
		t.Errorf("expected both user blocks; got:\n%s", got)
	}
	// Each entry should land with the correct M-code prefix and the
	// entry text wrapped.
	if !strings.Contains(got, "M0.") || !strings.Contains(got, "talk politely") {
		t.Errorf("expected M0 entry rendered; got:\n%s", got)
	}
	if !strings.Contains(got, "M3.") || !strings.Contains(got, "never disclose passwords") {
		t.Errorf("expected M3 entry rendered; got:\n%s", got)
	}
}

func TestOutputCodesSplicing(t *testing.T) {
	line := outputCodesLine([]string{"a", "b"}, []string{"c"})
	// Two extra M0 codes after M0; one extra M3 code after the last
	// static M3 letter (M3.f at time of writing, resolved at module
	// load).
	wantSubs := []string{"M0, M0.a, M0.b, M2.a", "M3.f, M3.g, M4.a"}
	for _, w := range wantSubs {
		if !strings.Contains(line, w) {
			t.Errorf("expected %q in output codes line; got %q", w, line)
		}
	}
}

func TestOutputCodesEmpty(t *testing.T) {
	if got := outputCodesLine(nil, nil); !strings.HasPrefix(got, "M0, M2.a") {
		t.Errorf("empty entries should give static codes; got %q", got)
	}
}

func TestRenderPolicyMalformedEntriesGracefulDegrade(t *testing.T) {
	// Operator hand-edited the row; entries column is invalid JSON.
	// Render must not panic and must collapse to the empty-block path.
	p := &store.AgentProfile{
		ID:        "p1",
		Enabled:   true,
		Remit:     "x",
		M0Entries: `not json`,
		M3Entries: `[invalid]`,
	}
	got := renderPolicy(context.Background(), p, "G")
	if strings.Contains(got, "User-defined") {
		t.Errorf("malformed entries should not render user blocks; got:\n%s", got)
	}
	// The remit still lands wrapped; only the entry decode failed.
	if !strings.Contains(got, `<adrian-untrusted id="G">x</adrian-untrusted id="G">`) {
		t.Errorf("expected wrapped remit even when entries are malformed; got:\n%s", got)
	}
}

func TestRenderFewShotUserWraps(t *testing.T) {
	got := renderFewShotUser("G")
	if !strings.Contains(got, `<adrian-untrusted id="G">`) {
		t.Errorf("few-shot user must wrap example trace under guid; got:\n%s", got)
	}
	if !strings.HasPrefix(got, "Classify this agent trace:") {
		t.Errorf("few-shot user must lead with the classify directive; got:\n%s", got)
	}
}

func TestParseTaxonomy(t *testing.T) {
	prompt := strings.Join([]string{
		"- M0.a alpha",
		"- M3.a one",
		"- M3.f six",
		"- M4.b two",
	}, "\n")
	codes, m0, m3 := parseTaxonomy(prompt)
	if m0 != 'a' {
		t.Errorf("highestM0 = %q, want 'a'", m0)
	}
	if m3 != 'f' {
		t.Errorf("highestM3 = %q, want 'f'", m3)
	}
	wantCodes := []string{"M0", "M0.a", "M3.a", "M3.f", "M4.b"}
	if len(codes) != len(wantCodes) {
		t.Fatalf("codes = %v, want %v", codes, wantCodes)
	}
	for i, c := range wantCodes {
		if codes[i] != c {
			t.Errorf("codes[%d] = %q, want %q", i, codes[i], c)
		}
	}
}
