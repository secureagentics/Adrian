// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"io"
	"net/http"
	"net/http/httptest"
	"os"
	"strings"
	"testing"
)

// --- a network error must stay a network error -------------------------

// errorBody fails partway through, the way a connection that drops
// mid-reply does.
type errorBody struct{ read bool }

func (b *errorBody) Read(p []byte) (int, error) {
	if b.read {
		return 0, io.ErrUnexpectedEOF
	}
	b.read = true
	n := copy(p, []byte(`{"choices":[{"message":{"content":"M0`))
	return n, nil
}

func (b *errorBody) Close() error { return nil }

type truncatingTransport struct{}

func (truncatingTransport) RoundTrip(*http.Request) (*http.Response, error) {
	return &http.Response{StatusCode: 200, Body: &errorBody{}, Header: http.Header{}}, nil
}

// TestMeterReportsReadErrors covers the meter turning a retryable
// network error into a permanent parse error. The meter reads the reply
// before the judge client does, so a drop mid-reply surfaces here
// first; swallowing it handed the judge a truncated body, which failed
// as "unexpected end of JSON input" and was never retried.
func TestMeterReportsReadErrors(t *testing.T) {
	meter := &usageMeter{base: truncatingTransport{}}
	client := &http.Client{Transport: meter}

	_, err := client.Get("http://example.invalid/v1/chat/completions")
	if err == nil {
		t.Fatal("a reply that failed mid-read was reported as a success")
	}
	if !isTransient(err) {
		t.Errorf("error is not recognised as retryable, so the case would be\n"+
			"recorded as a judge mistake instead of retried: %v", err)
	}
}

// TestMeterStillCountsGoodReplies guards the fix from overreaching: a
// complete reply must still be metered and passed on unchanged.
func TestMeterStillCountsGoodReplies(t *testing.T) {
	body := `{"usage":{"prompt_tokens":100,"completion_tokens":20},"choices":[]}`
	srv := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		io.WriteString(w, body)
	}))
	defer srv.Close()

	meter := &usageMeter{base: http.DefaultTransport}
	resp, err := (&http.Client{Transport: meter}).Get(srv.URL)
	if err != nil {
		t.Fatal(err)
	}
	got, _ := io.ReadAll(resp.Body)
	resp.Body.Close()

	if string(got) != body {
		t.Errorf("body was altered:\n got %s\nwant %s", got, body)
	}
	if u := meter.Snapshot(); u.Calls != 1 || u.InputTokens != 100 || u.OutputTokens != 20 {
		t.Errorf("usage not counted: %+v", u)
	}
}

// --- a label may not name a code the judge can never return ------------

func profileWith(expected, risks int) map[string]Profile {
	p := Profile{Remit: "a remit"}
	for i := 0; i < expected; i++ {
		p.Expected = append(p.Expected, "expected behaviour")
	}
	for i := 0; i < risks; i++ {
		p.Risks = append(p.Risks, "known risk")
	}
	return map[string]Profile{"cust": p}
}

func llmCase(id, profile, expected string, alsoOK ...string) Case {
	return Case{ID: id, Kind: "llm", Response: "x", Profile: profile, Expected: expected, AlsoOK: alsoOK}
}

// TestLabelsMustBeReachable covers the gap the stricter codePattern left
// open. LoadCases validates a case before the profiles are read, so it
// has to accept M0.a and M3.g from any case. Only here are the profiles
// known, so only here can a label be checked against the codes the
// judge will actually be offered.
func TestLabelsMustBeReachable(t *testing.T) {
	profiles := profileWith(1, 1) // reaches M0.a and M3.g, no further

	rejected := []Case{
		llmCase("user-code-without-profile", "", "M3.g"),
		llmCase("m0a-without-profile", "", "M0.a"),
		llmCase("beyond-expected", "cust", "M0.b"),
		llmCase("beyond-risks", "cust", "M3.h"),
		llmCase("bad-also-ok", "cust", "M0", "M3.i"),
	}
	for _, c := range rejected {
		if err := checkProfileNames([]Case{c}, profiles); err == nil {
			t.Errorf("%s: accepted a label no judge can return", c.ID)
		}
	}

	accepted := []Case{
		llmCase("first-expected", "cust", "M0.a"),
		llmCase("first-risk", "cust", "M3.g"),
		llmCase("static-code", "", "M3.f"),
		llmCase("static-code-under-profile", "cust", "M4.a"),
	}
	if err := checkProfileNames(accepted, profiles); err != nil {
		t.Errorf("rejected a reachable label: %v", err)
	}
}

// TestLabelsCheckedInsideSteps: a multi-step case carries its labels on
// the steps, so checking only the top level would miss them.
func TestLabelsCheckedInsideSteps(t *testing.T) {
	c := Case{
		ID: "multi", Kind: "", Profile: "cust",
		Steps: []CaseStep{
			{Kind: "llm", Response: "a", Expected: "M0"},
			{Kind: "llm", Response: "b", Expected: "M3.h"},
		},
	}
	if err := checkProfileNames([]Case{c}, profileWith(1, 1)); err == nil {
		t.Fatal("an unreachable label inside a step was accepted")
	} else if !strings.Contains(err.Error(), "multi#2") {
		t.Errorf("error should name the step that is wrong, got: %v", err)
	}
}

// TestRealTestdataLabelsAreReachable runs the check over the data that
// actually ships, so a label added later that no judge can answer fails
// here rather than scoring silently wrong on every run.
func TestRealTestdataLabelsAreReachable(t *testing.T) {
	profiles, err := LoadProfiles("testdata/profiles.json")
	if err != nil {
		t.Fatal(err)
	}
	for _, path := range []string{"testdata/cases.jsonl", "testdata/heldout.jsonl"} {
		f, err := os.Open(path)
		if err != nil {
			t.Fatal(err)
		}
		cases, err := LoadCases(f)
		f.Close()
		if err != nil {
			t.Fatalf("%s: %v", path, err)
		}
		if err := checkProfileNames(cases, profiles); err != nil {
			t.Errorf("%s: %v", path, err)
		}
	}
}

// --- the comparison must not call one flipped case a regression --------

func snapshotWith(s Scores) Snapshot {
	return Snapshot{Scores: s, Cases: map[string]caseStatus{}}
}

// TestOneFlippedCaseIsNotARegression covers what the BrokenTolerance fix
// left open: the safety rates were compared with a bare >, and one
// benign case flipping already raises the false-positive rate, so a
// noisy run still failed the comparison.
func TestOneFlippedCaseIsNotARegression(t *testing.T) {
	old := snapshotWith(Scores{Benign: 100, FalsePositives: 4, BlockCases: 130, MissedBlocks: 4})

	one := Compare(old, snapshotWith(Scores{Benign: 100, FalsePositives: 5, BlockCases: 130, MissedBlocks: 4}))
	if one.Regressed() {
		t.Error("one benign case flipping was called a regression")
	}

	avg := Compare(old, snapshotWith(Scores{Benign: 100, FalsePositives: 4.33, BlockCases: 130, MissedBlocks: 4}))
	if avg.Regressed() {
		t.Error("a 4.0 -> 4.33 average across runs was called a regression")
	}

	miss := Compare(old, snapshotWith(Scores{Benign: 100, FalsePositives: 4, BlockCases: 130, MissedBlocks: 5}))
	if miss.Regressed() {
		t.Error("one missed block more was called a regression")
	}
}

// TestRealSlideIsStillARegression is the other half: the tolerance must
// not swallow a change big enough to matter.
func TestRealSlideIsStillARegression(t *testing.T) {
	old := snapshotWith(Scores{Benign: 100, FalsePositives: 4, BlockCases: 130, MissedBlocks: 4})

	fp := Compare(old, snapshotWith(Scores{Benign: 100, FalsePositives: 7, BlockCases: 130, MissedBlocks: 4}))
	if !fp.Regressed() {
		t.Error("three more false positives should be a regression")
	}

	mb := Compare(old, snapshotWith(Scores{Benign: 100, FalsePositives: 4, BlockCases: 130, MissedBlocks: 9}))
	if !mb.Regressed() {
		t.Error("five more missed blocks should be a regression")
	}
}

// --- the report must pin the profiles it was judged against ------------

// TestMetaRecordsProfiles: without the profiles fingerprint, editing a
// profile changes what the profile cases are measured against while the
// report still looks like the same setup.
func TestMetaRecordsProfiles(t *testing.T) {
	sum, err := fileSHA256("testdata/profiles.json")
	if err != nil {
		t.Fatal(err)
	}
	m := Meta{ProfilesFile: "testdata/profiles.json", ProfilesSHA256: sum}
	if m.ProfilesSHA256 == "" {
		t.Fatal("profiles fingerprint is empty")
	}

	// Any edit to the file must move the hash.
	data, err := os.ReadFile("testdata/profiles.json")
	if err != nil {
		t.Fatal(err)
	}
	tmp := t.TempDir() + "/profiles.json"
	if err := os.WriteFile(tmp, append(data, ' '), 0o600); err != nil {
		t.Fatal(err)
	}
	edited, err := fileSHA256(tmp)
	if err != nil {
		t.Fatal(err)
	}
	if edited == sum {
		t.Error("an edited profiles file produced the same fingerprint")
	}
}

// --- the gate must read the average, not any single run -----------------

// runReport is one run's numbers, written directly rather than scored
// from results: the point here is what gateMetrics does with them.
func runReport(blockCases, missedBlocks, benign, falsePositives, blockErrors int) Report {
	return Report{
		Total:          blockCases + benign,
		Benign:         benign,
		FalsePositives: falsePositives,
		Violations:     blockCases,
		MissedBlocks:   missedBlocks,
		Confusion:      map[string]map[string]int{"block": {"error": blockErrors}},
	}
}

// TestMultiRunGateCombinesRuns covers the path the gate actually takes
// in use. The judge is not deterministic, so the README tells you to
// gate with -runs, which means MultiReport.gateMetrics -- not the
// single-run one -- is what decides pass or fail on a real run.
//
// Missed blocks take the worst run and everything else averages. A
// customer does not get the average; they get one run.
func TestMultiRunGateCombinesRuns(t *testing.T) {
	m := MultiReport{Runs: []Report{
		runReport(40, 2, 100, 5, 0),
		runReport(40, 4, 100, 4, 0),
		runReport(40, 3, 100, 6, 0),
	}}

	g := m.gateMetrics()
	if g.missedBlocks != 4 {
		t.Errorf("missed blocks = %v, want 4 (the worst run, not the average of 3)", g.missedBlocks)
	}
	if g.falsePositives != 5 {
		t.Errorf("false positives averaged to %v, want 5 (5+4+6)/3", g.falsePositives)
	}
	if g.blockCases != 40 || g.benign != 100 {
		t.Errorf("denominators should survive: %+v", g)
	}
}

// TestWorstRunDenominatorComesFromTheSameRun: taking the worst
// numerator from one run and a denominator from another would invent a
// rate no run produced. Here the second run is worst by rate (3 of 10)
// even though the first has more misses (5 of 100).
func TestWorstRunDenominatorComesFromTheSameRun(t *testing.T) {
	m := MultiReport{Runs: []Report{
		runReport(100, 5, 50, 0, 0), // 5%
		runReport(10, 3, 50, 0, 0),  // 30% <- worst by rate
	}}

	g := m.gateMetrics()
	if g.missedBlocks != 3 || g.blockCases != 10 {
		t.Errorf("want 3 of 10 (the worst run, both halves), got %v of %v", g.missedBlocks, g.blockCases)
	}
}

// TestOneBadRunFailsTheGateOnMissedBlocks is the point of the change.
// Two clean runs and one bad one: the average would pass a 3% limit,
// the worst run does not. Four blocks got through for whoever hit that
// run, and no amount of averaging undoes it.
func TestOneBadRunFailsTheGateOnMissedBlocks(t *testing.T) {
	limit := Thresholds{MaxMissedBlocksPct: 3, MaxFalsePositivesPct: -1, MinTierAccuracyPct: -1, MaxErrorPct: -1}
	m := MultiReport{Runs: []Report{
		runReport(93, 0, 121, 1, 0),
		runReport(93, 4, 121, 1, 0), // 4.3%
		runReport(93, 3, 121, 1, 0),
	}}

	if avg := (0 + 4 + 3) / 3.0 / 93 * 100; avg > 3 {
		t.Fatalf("the average should be inside the limit, or this test proves nothing (got %.1f%%)", avg)
	}
	if CheckGate(m.gateMetrics(), limit).Passed {
		t.Error("a run missing 4 of 93 blocks must fail a 3% limit, whatever the other runs did")
	}
}

// TestOneNoisyRunDoesNotFailTheGateOnFalsePositives is the other half
// of the asymmetry. Over-blocking is a cost, not a breach, so one bad
// run should not fire the gate: holding it to the worst case would make
// -runs useless against ordinary judge noise.
func TestOneNoisyRunDoesNotFailTheGateOnFalsePositives(t *testing.T) {
	limit := Thresholds{MaxMissedBlocksPct: -1, MaxFalsePositivesPct: 5, MinTierAccuracyPct: -1, MaxErrorPct: -1}
	m := MultiReport{Runs: []Report{
		runReport(40, 0, 100, 2, 0),
		runReport(40, 0, 100, 9, 0), // 9% on its own
		runReport(40, 0, 100, 2, 0),
	}}

	// (2 + 9 + 2) / 3 = 4.33 of 100 = 4.3%, inside the limit.
	if !CheckGate(m.gateMetrics(), limit).Passed {
		t.Error("false positives should average, so one noisy run does not fail the gate")
	}
}

// TestMultiRunGateCountsErroredBlockCases: the single-run metrics treat
// a block case that got no verdict as a missed block, since nothing was
// stopped. That must survive the multi-run combine, or a judge that
// fails intermittently passes a gate it would fail on one run.
func TestMultiRunGateCountsErroredBlockCases(t *testing.T) {
	// 38 answered block cases with no misses, plus 2 that errored.
	m := MultiReport{Runs: []Report{
		runReport(38, 0, 100, 0, 2),
		runReport(38, 0, 100, 0, 2),
	}}

	g := m.gateMetrics()
	if g.missedBlocks != 2 {
		t.Errorf("errored block cases should count as missed: got %v, want 2", g.missedBlocks)
	}
	if g.blockCases != 40 {
		t.Errorf("errored block cases should count in the denominator: got %v, want 40", g.blockCases)
	}

	limit := Thresholds{MaxMissedBlocksPct: 2, MaxFalsePositivesPct: -1, MinTierAccuracyPct: -1, MaxErrorPct: -1}
	if CheckGate(g, limit).Passed {
		t.Error("5% missed blocks from errors alone must fail a 2% limit")
	}
}

// TestMultiRunGateWithNoRuns: an empty report must not divide by zero
// or report a clean sheet.
func TestMultiRunGateWithNoRuns(t *testing.T) {
	g := MultiReport{}.gateMetrics()
	if g.total != 0 || g.blockCases != 0 {
		t.Errorf("an empty report should be all zero, got %+v", g)
	}
	// Every check has a zero denominator, so each is skipped and noted
	// rather than silently passing on no evidence.
	res := CheckGate(g, Thresholds{MaxMissedBlocksPct: 0, MaxFalsePositivesPct: 0, MinTierAccuracyPct: 100, MaxErrorPct: 0})
	for _, c := range res.Checks {
		if !strings.Contains(c, "skipped") {
			t.Errorf("check should be skipped with no cases: %q", c)
		}
	}
}
