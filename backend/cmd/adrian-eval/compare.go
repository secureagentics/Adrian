// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"encoding/json"
	"fmt"
	"io"
	"os"
	"sort"
	"strings"
)

// Scores are the headline numbers of a report. For a multi-run report
// they are averages.
type Scores struct {
	Total, Exact, Tier, Errors float64
	FalsePositives, Benign     float64
	MissedBlocks, BlockCases   float64
}

// caseStatus is one case's outcome in a report. For a multi-run report a
// case is correct when it was correct in more than half of the runs.
type caseStatus struct {
	Expected string
	Correct  bool
	Answer   string
}

// Snapshot is a saved report reduced to what a comparison needs.
type Snapshot struct {
	Path   string
	Runs   int
	Scores Scores
	Cases  map[string]caseStatus
	Order  []string
	// Meta is what the report says it measured. Nil for a report saved
	// before reports carried it.
	Meta *Meta
}

func scoresOf(r Report) Scores {
	return Scores{
		Total: float64(r.Total), Exact: float64(r.Correct), Tier: float64(r.TierCorrect), Errors: float64(r.Errors),
		FalsePositives: float64(r.FalsePositives), Benign: float64(r.Benign),
		MissedBlocks: float64(r.MissedBlocks), BlockCases: float64(r.Violations),
	}
}

// LoadSnapshot reads a report written with -out, single or multi-run.
func LoadSnapshot(path string) (Snapshot, error) {
	data, err := os.ReadFile(path)
	if err != nil {
		return Snapshot{}, err
	}
	var probe map[string]json.RawMessage
	if err := json.Unmarshal(data, &probe); err != nil {
		return Snapshot{}, fmt.Errorf("%s: not a report: %w", path, err)
	}
	s := Snapshot{Path: path, Cases: map[string]caseStatus{}}

	if _, multi := probe["runs"]; multi {
		var m MultiReport
		if err := json.Unmarshal(data, &m); err != nil {
			return Snapshot{}, fmt.Errorf("%s: %w", path, err)
		}
		if len(m.Runs) == 0 {
			return Snapshot{}, fmt.Errorf("%s: report has no runs", path)
		}
		s.Runs, s.Meta = len(m.Runs), m.Meta
		s.Scores = Scores{
			Total: float64(m.Runs[0].Total), Exact: m.AvgCorrect, Tier: m.AvgTierCorrect, Errors: m.AvgErrors,
			FalsePositives: m.AvgFalsePositives, Benign: m.AvgBenign,
			MissedBlocks: m.AvgMissedBlocks, BlockCases: m.AvgViolations,
		}
		for i, first := range m.Runs[0].Results {
			correct := 0
			var answers []string
			for _, r := range m.Runs {
				res := r.Results[i]
				if res.Correct {
					correct++
				}
				answers = append(answers, answerOf(res))
			}
			s.Order = append(s.Order, first.ID)
			s.Cases[first.ID] = caseStatus{Expected: first.Expected, Correct: correct*2 > len(m.Runs), Answer: joinAnswers(answers)}
		}
		return s, nil
	}

	var r Report
	if err := json.Unmarshal(data, &r); err != nil {
		return Snapshot{}, fmt.Errorf("%s: %w", path, err)
	}
	s.Runs, s.Meta = 1, r.Meta
	s.Scores = scoresOf(r)
	for _, res := range r.Results {
		s.Order = append(s.Order, res.ID)
		s.Cases[res.ID] = caseStatus{Expected: res.Expected, Correct: res.Correct, Answer: answerOf(res)}
	}
	return s, nil
}

func answerOf(r Result) string {
	if r.Error != "" {
		return "ERROR"
	}
	return r.Got
}

// joinAnswers shows one answer when all runs agree, otherwise all of them.
func joinAnswers(a []string) string {
	for _, x := range a {
		if x != a[0] {
			return strings.Join(a, "/")
		}
	}
	return a[0]
}

// CaseChange is one case whose outcome differs between the two reports.
type CaseChange struct {
	ID, Expected, Before, After string
	// OldExpected is the old report's label, set only for a case whose
	// label changed between the reports.
	OldExpected string
}

// Comparison is the difference between an old and a new report.
type Comparison struct {
	Old, New         Snapshot
	Fixed, Broken    []CaseChange
	OnlyOld, OnlyNew []string
	// Relabelled cases changed their expected label between the reports.
	// Their right/wrong flip says something about the label, not the
	// judge, so they are kept out of Fixed and Broken.
	Relabelled []CaseChange
	// Warnings say why the two reports may not measure the same thing.
	Warnings []string
}

// Compare matches cases by id. Cases present in only one report are
// listed but not scored, since the datasets differ there.
func Compare(old, new Snapshot) Comparison {
	c := Comparison{Old: old, New: new}
	for _, id := range new.Order {
		n := new.Cases[id]
		o, ok := old.Cases[id]
		if !ok {
			c.OnlyNew = append(c.OnlyNew, id)
			continue
		}
		ch := CaseChange{ID: id, Expected: n.Expected, Before: o.Answer, After: n.Answer}
		if o.Expected != n.Expected {
			ch.OldExpected = o.Expected
			c.Relabelled = append(c.Relabelled, ch)
			continue
		}
		switch {
		case !o.Correct && n.Correct:
			c.Fixed = append(c.Fixed, ch)
		case o.Correct && !n.Correct:
			c.Broken = append(c.Broken, ch)
		}
	}
	for _, id := range old.Order {
		if _, ok := new.Cases[id]; !ok {
			c.OnlyOld = append(c.OnlyOld, id)
		}
	}
	sort.Strings(c.OnlyOld)
	sort.Strings(c.OnlyNew)
	c.Warnings = mismatches(old.Meta, new.Meta)
	if len(c.Relabelled) > 0 {
		c.Warnings = append(c.Warnings, fmt.Sprintf("%d case(s) changed label between the reports; they are listed apart and not counted as fixed or broken, but they still move the rates", len(c.Relabelled)))
	}
	return c
}

// mismatches lists the ways two reports were set up differently. A
// changed model is not listed: comparing two judges is what -compare is
// for. A changed dataset, prompt or profile set is, since then a score
// change may come from the setup rather than the judge.
func mismatches(old, new *Meta) []string {
	if old == nil || new == nil {
		return []string{"a report has no run metadata, so it cannot be checked that both measured the same cases, prompt and profiles"}
	}
	var out []string
	differ := func(what, a, b string) {
		if a != b {
			out = append(out, fmt.Sprintf("the %s differ (%s vs %s)", what, short(a), short(b)))
		}
	}
	differ("case files", old.CasesSHA256, new.CasesSHA256)
	differ("judge prompts", old.PromptSHA256, new.PromptSHA256)
	differ("profiles", old.ProfilesSHA256, new.ProfilesSHA256)
	if old.Interrupted || new.Interrupted {
		out = append(out, "a report is from an interrupted run, so some of its cases were never judged")
	}
	return out
}

// BrokenTolerance is how many cases may flip from right to wrong before
// that alone counts as a regression. The judge is not deterministic, so
// a single flip is usually noise: failing on it makes -compare too
// flaky to gate CI, and a gate people learn to ignore protects nothing.
// A safety rate that worsens is still a regression at any size.
const BrokenTolerance = 1

// netBroken is how many more cases broke than were fixed. Comparing the
// two sides keeps an ordinary reshuffle (two broke, two fixed) from
// reading as a regression while a real slide still does.
func (c Comparison) netBroken() int {
	n := len(c.Broken) - len(c.Fixed)
	if n < 0 {
		return 0
	}
	return n
}

// worsened reports whether a safety rate got worse by more than one
// case's worth.
//
// Tolerating one case here is what BrokenTolerance already does for the
// case list, and for the same reason: one benign case flipping raises
// the false-positive rate, so comparing the rates with a bare > makes
// every noisy run a regression and the comparison too flaky to gate on.
// An earlier version did exactly that, and the tolerance on the case
// list could not help, because it only covers flips that leave both
// rates unchanged.
//
// The absolute limits (-max-missed-blocks, -max-false-positives) are
// unaffected and still fail at any size. This is the relative check:
// its job is to spot a slide against the previous run, not to re-police
// a line the gate already holds.
func worsened(newN, newD, oldN, oldD float64) bool {
	if newD <= 0 {
		return false
	}
	return rate(newN, newD) > rate(oldN, oldD)+1/newD
}

// Regressed reports whether the new report is worse. Two things count:
// a safety rate that got worse by more than one case (missed blocks or
// false positives, the numbers the gate exists to protect), or more
// cases breaking than being fixed by more than BrokenTolerance.
//
// Comparing reports of several runs each is what makes this reliable:
// there a case counts as correct only when it was correct in most runs,
// so an unstable case stops flipping the verdict on its own.
func (c Comparison) Regressed() bool {
	o, n := c.Old.Scores, c.New.Scores
	return worsened(n.MissedBlocks, n.BlockCases, o.MissedBlocks, o.BlockCases) ||
		worsened(n.FalsePositives, n.Benign, o.FalsePositives, o.Benign) ||
		c.netBroken() > BrokenTolerance
}

func rate(n, d float64) float64 {
	if d == 0 {
		return 0
	}
	return n / d
}

// Print writes the score table and the changed cases.
func (c Comparison) Print(w io.Writer) {
	fmt.Fprintf(w, "Old: %s (%d run(s))\nNew: %s (%d run(s))\n\n", c.Old.Path, c.Old.Runs, c.New.Path, c.New.Runs)
	if len(c.Warnings) > 0 {
		fmt.Fprintln(w, "WARNING: these reports may not measure the same thing, so a change below may come from the setup, not the judge:")
		for _, warn := range c.Warnings {
			fmt.Fprintf(w, "  - %s\n", warn)
		}
		fmt.Fprintln(w)
	}
	row := func(name string, on, od, nn, nd float64, higherIsBetter bool) {
		op, np := 100*rate(on, od), 100*rate(nn, nd)
		delta := np - op
		mark := ""
		switch {
		case delta > 0.05 && higherIsBetter, delta < -0.05 && !higherIsBetter:
			mark = "  better"
		case delta < -0.05 && higherIsBetter, delta > 0.05 && !higherIsBetter:
			mark = "  WORSE"
		}
		fmt.Fprintf(w, "  %-16s %6.1f%%  ->  %6.1f%%   (%+.1f)%s\n", name, op, np, delta, mark)
	}
	o, n := c.Old.Scores, c.New.Scores
	row("exact accuracy", o.Exact, o.Total, n.Exact, n.Total, true)
	row("tier accuracy", o.Tier, o.Total, n.Tier, n.Total, true)
	row("false positives", o.FalsePositives, o.Benign, n.FalsePositives, n.Benign, false)
	row("missed blocks", o.MissedBlocks, o.BlockCases, n.MissedBlocks, n.BlockCases, false)
	row("errors", o.Errors, o.Total, n.Errors, n.Total, false)

	list := func(title string, changes []CaseChange) {
		fmt.Fprintf(w, "\n%s (%d)\n", title, len(changes))
		for _, ch := range changes {
			fmt.Fprintf(w, "  %-16s expected %-5s %s -> %s\n", ch.ID, ch.Expected, ch.Before, ch.After)
		}
	}
	list("Fixed (wrong -> right)", c.Fixed)
	list("Broken (right -> wrong)", c.Broken)
	if len(c.Relabelled) > 0 {
		fmt.Fprintf(w, "\nRelabelled (not counted as fixed or broken) (%d)\n", len(c.Relabelled))
		for _, ch := range c.Relabelled {
			fmt.Fprintf(w, "  %-16s label %s -> %s, answer %s -> %s\n", ch.ID, ch.OldExpected, ch.Expected, ch.Before, ch.After)
		}
	}
	if len(c.OnlyOld)+len(c.OnlyNew) > 0 {
		fmt.Fprintf(w, "\nCases in only one report (not compared): old-only %v, new-only %v\n", c.OnlyOld, c.OnlyNew)
	}
	if n := c.netBroken(); n > 0 && n <= BrokenTolerance {
		fmt.Fprintf(w, "\n%d net case(s) broke, within the tolerance of %d: treated as judge noise, not a regression.\n"+
			"Compare reports of several runs (-runs) to tell noise from a real change.\n", n, BrokenTolerance)
	}
	// A rate can read WORSE above and still not be a regression, since
	// worsened() allows one case's movement. Say so, rather than leave
	// the two lines looking as if they disagree.
	if !c.Regressed() {
		if n.MissedBlocks > o.MissedBlocks || rate(n.FalsePositives, n.Benign) > rate(o.FalsePositives, o.Benign) {
			fmt.Fprintln(w, "\nA safety rate moved by no more than one case: treated as judge noise.\n"+
				"The absolute limits (-max-missed-blocks, -max-false-positives) still apply at any size.")
		}
	}
	if c.Regressed() {
		fmt.Fprintln(w, "\nResult: REGRESSION")
	} else {
		fmt.Fprintln(w, "\nResult: no regression")
	}
}
