// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package engine

import (
	"regexp"
	"sort"
	"testing"

	"github.com/secureagentics/Adrian/backend/internal/alerts"
)

// builtinCodePattern matches the built-in subcode bullets in the
// judge prompt's VIOLATES section, e.g. "- M2.f Dispatch without ...".
var builtinCodePattern = regexp.MustCompile(`(?m)^- (M[234]\.[a-z]) `)

// TestPromptCodesHaveAlertEntries keeps the judge prompt and the
// user-facing alerts bundle in sync. Every subcode the classifier can
// emit must have a curated entry, and every curated entry must be a
// code the classifier is actually told about.
func TestPromptCodesHaveAlertEntries(t *testing.T) {
	bundle, err := alerts.Get()
	if err != nil {
		t.Fatalf("alerts.Get: %v", err)
	}

	inPrompt := map[string]bool{}
	for _, m := range builtinCodePattern.FindAllStringSubmatch(systemPrompt, -1) {
		inPrompt[m[1]] = true
	}
	if len(inPrompt) == 0 {
		t.Fatal("found no built-in codes in system_prompt.md; has the format changed?")
	}

	var missing, orphaned []string
	for code := range inPrompt {
		if _, ok := bundle.Alerts[code]; !ok {
			missing = append(missing, code)
		}
	}
	for code := range bundle.Alerts {
		if !inPrompt[code] {
			orphaned = append(orphaned, code)
		}
	}
	sort.Strings(missing)
	sort.Strings(orphaned)

	if len(missing) > 0 {
		t.Errorf("codes in system_prompt.md but missing from alerts.json: %v", missing)
	}
	if len(orphaned) > 0 {
		t.Errorf("codes in alerts.json but not in system_prompt.md: %v", orphaned)
	}
}

// TestAlertEntriesAreConsistent checks each curated entry agrees with
// its own key and with the bundle's per-severity default action.
func TestAlertEntriesAreConsistent(t *testing.T) {
	bundle, err := alerts.Get()
	if err != nil {
		t.Fatalf("alerts.Get: %v", err)
	}
	for key, a := range bundle.Alerts {
		if a.Code != key {
			t.Errorf("%s: entry code %q does not match its key", key, a.Code)
		}
		if a.Severity != key[:2] {
			t.Errorf("%s: severity %q does not match code prefix", key, a.Severity)
		}
		if want := bundle.DefaultAction[a.Severity]; a.DefaultAction != want {
			t.Errorf("%s: default_action %q, want %q for %s", key, a.DefaultAction, want, a.Severity)
		}
		if a.Subcategory == "" || a.Description == "" || a.SeverityLabel == "" {
			t.Errorf("%s: empty user-visible field", key)
		}
	}
}
