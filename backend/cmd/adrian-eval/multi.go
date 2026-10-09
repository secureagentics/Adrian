// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"fmt"
	"io"
)

// CaseAnswers lists what the judge answered for one case in each run.
type CaseAnswers struct {
	ID       string   `json:"id"`
	Expected string   `json:"expected"`
	Answers  []string `json:"answers"`
}

// MultiReport summarises several runs over the same cases. The judge is
// not fully deterministic, so averages are more honest than one run, and
// cases whose answer changes between runs show where it is unsure.
type MultiReport struct {
	Runs              []Report      `json:"runs"`
	AvgCorrect        float64       `json:"avg_correct"`
	AvgTierCorrect    float64       `json:"avg_tier_correct"`
	AvgFalsePositives float64       `json:"avg_false_positives"`
	AvgBenign         float64       `json:"avg_benign_cases"`
	AvgMissedBlocks   float64       `json:"avg_missed_blocks"`
	AvgViolations     float64       `json:"avg_block_cases"`
	AvgErrors         float64       `json:"avg_errors"`
	Unstable          []CaseAnswers `json:"unstable"`
	WrongEveryRun     []CaseAnswers `json:"wrong_every_run"`
	// Errored lists cases that got no answer in at least one run. They
	// are kept apart from Unstable: a timeout is not the judge changing
	// its mind.
	Errored []CaseAnswers `json:"errored,omitempty"`
	Gate    *GateResult   `json:"gate,omitempty"`
	Meta    *Meta         `json:"meta,omitempty"`
	Usage   *Usage        `json:"usage,omitempty"`
}

// Summarise combines the reports of several runs over the same cases.
func Summarise(reps []Report) MultiReport {
	m := MultiReport{Runs: reps}
	if len(reps) == 0 {
		return m
	}
	n := float64(len(reps))
	for _, r := range reps {
		m.AvgCorrect += float64(r.Correct) / n
		m.AvgTierCorrect += float64(r.TierCorrect) / n
		m.AvgFalsePositives += float64(r.FalsePositives) / n
		m.AvgBenign += float64(r.Benign) / n
		m.AvgMissedBlocks += float64(r.MissedBlocks) / n
		m.AvgViolations += float64(r.Violations) / n
		m.AvgErrors += float64(r.Errors) / n
	}

	// Every run covers the same cases in the same order.
	for i, first := range reps[0].Results {
		ca := CaseAnswers{ID: first.ID, Expected: first.Expected}
		wrongAll, errored := true, false
		seen := map[string]bool{}
		for _, r := range reps {
			res := r.Results[i]
			answer := res.Got
			if res.Error != "" {
				answer, errored = "ERROR", true
			} else {
				seen[answer] = true
			}
			ca.Answers = append(ca.Answers, answer)
			if res.Correct {
				wrongAll = false
			}
		}
		// Unstable means the judge gave different answers, so only real
		// answers count; an error in one run is listed under Errored.
		if len(seen) > 1 {
			m.Unstable = append(m.Unstable, ca)
		}
		if errored {
			m.Errored = append(m.Errored, ca)
		}
		if wrongAll {
			m.WrongEveryRun = append(m.WrongEveryRun, ca)
		}
	}
	return m
}

func avgPct(n, d float64) string {
	if d == 0 {
		return "n/a"
	}
	return fmt.Sprintf("%.1f%%", 100*n/d)
}

// Print writes a short summary per run, the averages, and the cases that
// changed answer or were wrong in every run.
func (m MultiReport) Print(w io.Writer) {
	if len(m.Runs) == 0 {
		return
	}
	total := float64(m.Runs[0].Total)
	fmt.Fprintf(w, "%d runs, %d cases each\n\n", len(m.Runs), m.Runs[0].Total)
	for i, r := range m.Runs {
		fmt.Fprintf(w, "Run %d: exact %d/%d, tier %d/%d, false positives %d/%d, missed blocks %d/%d, errors %d\n",
			i+1, r.Correct, r.Total, r.TierCorrect, r.Total, r.FalsePositives, r.Benign, r.MissedBlocks, r.Violations, r.Errors)
	}
	fmt.Fprintf(w, "\nAverage exact accuracy:   %.1f/%d (%s)\n", m.AvgCorrect, m.Runs[0].Total, avgPct(m.AvgCorrect, total))
	fmt.Fprintf(w, "Average tier accuracy:    %.1f/%d (%s)\n", m.AvgTierCorrect, m.Runs[0].Total, avgPct(m.AvgTierCorrect, total))
	fmt.Fprintf(w, "Average false positives:  %.1f/%.0f (%s)\n", m.AvgFalsePositives, m.AvgBenign, avgPct(m.AvgFalsePositives, m.AvgBenign))
	fmt.Fprintf(w, "Average missed blocks:    %.1f/%.0f (%s)\n", m.AvgMissedBlocks, m.AvgViolations, avgPct(m.AvgMissedBlocks, m.AvgViolations))
	fmt.Fprintf(w, "Average errors:           %.1f\n", m.AvgErrors)
	if m.Usage != nil {
		fmt.Fprintf(w, "Usage (all runs):         %s\n", m.Usage)
	}
	if m.Meta != nil {
		m.Meta.Print(w)
	}

	printList := func(title string, list []CaseAnswers) {
		fmt.Fprintf(w, "\n%s (%d)\n", title, len(list))
		for _, c := range list {
			fmt.Fprintf(w, "  %-16s expected %-5s answers %v\n", c.ID, c.Expected, c.Answers)
		}
	}
	printList("Unstable cases: the judge gave different answers between runs", m.Unstable)
	printList("Wrong in every run", m.WrongEveryRun)
	if len(m.Errored) > 0 {
		printList("Got no answer in some run (timeout, network or a reply with no M-code)", m.Errored)
	}
}
