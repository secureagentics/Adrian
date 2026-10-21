// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package main

import (
	"bufio"
	"encoding/json"
	"fmt"
	"io"
	"regexp"
	"strings"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// Case is one labelled test for the judge. It is one JSON object per
// line in the cases file.
//
// kind "llm" describes what the model reasoned and which tools it wants
// to call. kind "tool" describes a tool that actually ran, with its
// input and output.
type Case struct {
	ID                string         `json:"id"`
	Kind              string         `json:"kind"`
	AgentSystemPrompt string         `json:"agent_system_prompt"`
	UserInstruction   string         `json:"user_instruction"`
	Reasoning         string         `json:"reasoning"`
	Response          string         `json:"response"`
	ToolCalls         []CaseToolCall `json:"tool_calls"`
	ToolName          string         `json:"tool_name"`
	Input             string         `json:"input"`
	Output            string         `json:"output"`
	// Steps turns this into a multi-step case: the events are judged in
	// order and share one conversation, so each step sees the earlier
	// ones as history, exactly as the backend's sliding window does in
	// production. Each step is graded separately.
	Steps []CaseStep `json:"steps,omitempty"`
	// Profile names an agent profile from the profiles file. The judge
	// resolves it from the database and renders that customer's remit
	// and custom entries into its system prompt, so the same action can
	// be in scope for one agent and a violation for another. Empty means
	// the generic remit.
	Profile  string   `json:"profile,omitempty"`
	Expected string   `json:"expected"`
	AlsoOK   []string `json:"also_ok"`
	Note     string   `json:"note"`
	Tags     []string `json:"tags"`

	// conversation groups the steps of one multi-step case. Empty means
	// the case is its own conversation.
	conversation string
}

// CaseStep is one event of a multi-step case. It carries the same
// event fields as a single case, plus its own expected label.
type CaseStep struct {
	Kind      string         `json:"kind"`
	Reasoning string         `json:"reasoning"`
	Response  string         `json:"response"`
	ToolCalls []CaseToolCall `json:"tool_calls"`
	ToolName  string         `json:"tool_name"`
	Input     string         `json:"input"`
	Output    string         `json:"output"`
	Expected  string         `json:"expected"`
	AlsoOK    []string       `json:"also_ok"`
	// Note explains this step's label. Empty means the case's note.
	Note string `json:"note"`
	// Tags are added to the case's tags for this step only. Put a tag
	// that describes one step here (exfil on the step that leaks, not
	// on the harmless step before it), so the by-tag scores count each
	// step under what it actually is.
	Tags []string `json:"tags"`
}

// CaseToolCall is a tool call the model wants to make (kind "llm").
type CaseToolCall struct {
	Name string `json:"name"`
	Args string `json:"args"`
}

// codePattern is the set of M-codes a case may be LABELLED with. It is
// deliberately stricter than the engine's own parser: that one must
// accept whatever the judge answers, including a bare tier such as
// "M3", while a label in testdata is written by hand and a typo there
// scores silently against a code the judge can never return.
//
// The set follows LABEL_GUIDE.md. Built-in: M0, M2.a-g, M3.a-f,
// M4.a-e. A profile adds its own, so M0 takes an optional letter for
// an expected behaviour (M0.a, M0.b, ...) and M3 runs to .z for a
// user-defined risk (M3.g onward).
//
// This rejects a bare M2/M3/M4, M2.h, M4.f, M1.a, M5.a and M3_a.
var codePattern = regexp.MustCompile(`^(M0(\.[a-z])?|M2\.[a-g]|M3\.[a-z]|M4\.[a-e])$`)

func (c Case) validate() error {
	if c.ID == "" {
		return fmt.Errorf("id is required")
	}
	if len(c.Steps) > 0 {
		if c.Expected != "" {
			return fmt.Errorf("a multi-step case has no top-level expected; put one on each step")
		}
		if len(c.Steps) < 2 {
			return fmt.Errorf("a multi-step case needs at least 2 steps")
		}
		// These belong on a step. At the top level they would be dropped
		// without a word, so a case could look labelled or populated in
		// the file while the judge and the scorer never see it.
		for field, set := range map[string]bool{
			"kind": c.Kind != "", "reasoning": c.Reasoning != "", "response": c.Response != "",
			"tool_calls": len(c.ToolCalls) > 0, "tool_name": c.ToolName != "",
			"input": c.Input != "", "output": c.Output != "", "also_ok": len(c.AlsoOK) > 0,
		} {
			if set {
				return fmt.Errorf("a multi-step case has no top-level %s; put it on a step", field)
			}
		}
		for i, st := range c.Steps {
			if err := st.asCase(c, i).validate(); err != nil {
				return fmt.Errorf("step %d: %w", i+1, err)
			}
		}
		return nil
	}
	switch c.Kind {
	case "llm":
		if c.Reasoning == "" && c.Response == "" && len(c.ToolCalls) == 0 {
			return fmt.Errorf("kind llm needs reasoning, response or tool_calls")
		}
	case "tool":
		if c.ToolName == "" {
			return fmt.Errorf("kind tool needs tool_name")
		}
	default:
		return fmt.Errorf("kind must be \"llm\" or \"tool\", got %q", c.Kind)
	}
	if !codePattern.MatchString(c.Expected) {
		return fmt.Errorf("expected %q is not a valid M-code (want M0, M2.a-g, M3.a-f, M4.a-e; M0.a+/M3.g+ for profile codes)", c.Expected)
	}
	for _, code := range c.AlsoOK {
		if !codePattern.MatchString(code) {
			return fmt.Errorf("also_ok %q is not a valid M-code (want M0, M2.a-g, M3.a-f, M4.a-e; M0.a+/M3.g+ for profile codes)", code)
		}
	}
	return nil
}

// LoadCases reads JSONL, one case per line. Blank lines are skipped.
// Unknown fields are rejected so a typo such as "expcted" fails loudly
// instead of silently scoring against an empty label.
func LoadCases(r io.Reader) ([]Case, error) {
	sc := bufio.NewScanner(r)
	sc.Buffer(make([]byte, 0, 64*1024), 4*1024*1024)

	var cases []Case
	seen := map[string]bool{}
	for line := 1; sc.Scan(); line++ {
		text := strings.TrimSpace(sc.Text())
		if text == "" {
			continue
		}
		var c Case
		dec := json.NewDecoder(strings.NewReader(text))
		dec.DisallowUnknownFields()
		if err := dec.Decode(&c); err != nil {
			return nil, fmt.Errorf("line %d: %w", line, err)
		}
		if err := c.validate(); err != nil {
			return nil, fmt.Errorf("line %d (%s): %w", line, c.ID, err)
		}
		if seen[c.ID] {
			return nil, fmt.Errorf("line %d: duplicate id %q", line, c.ID)
		}
		seen[c.ID] = true
		cases = append(cases, c)
	}
	if err := sc.Err(); err != nil {
		return nil, err
	}
	return cases, nil
}

// asCase renders step i as a standalone Case, inheriting the parent's
// agent prompt, user instruction and profile, its note when the step
// has none, and its tags (plus the step's own). The id carries the step
// number so each step is reported separately.
func (st CaseStep) asCase(parent Case, i int) Case {
	note := st.Note
	if note == "" {
		note = parent.Note
	}
	return Case{
		ID:                fmt.Sprintf("%s#%d", parent.ID, i+1),
		Kind:              st.Kind,
		AgentSystemPrompt: parent.AgentSystemPrompt,
		UserInstruction:   parent.UserInstruction,
		Profile:           parent.Profile,
		Reasoning:         st.Reasoning,
		Response:          st.Response,
		ToolCalls:         st.ToolCalls,
		ToolName:          st.ToolName,
		Input:             st.Input,
		Output:            st.Output,
		Expected:          st.Expected,
		AlsoOK:            st.AlsoOK,
		Note:              note,
		Tags:              append(append([]string{}, parent.Tags...), st.Tags...),
	}
}

// Unroll returns the cases to judge, in order. A single case returns
// itself; a multi-step case returns one case per step. Every returned
// case shares the parent's id as its conversation, so the steps chain
// through the sliding window while separate cases stay isolated.
func (c Case) Unroll() []Case {
	if len(c.Steps) == 0 {
		return []Case{c}
	}
	out := make([]Case, 0, len(c.Steps))
	for i, st := range c.Steps {
		step := st.asCase(c, i)
		step.conversation = c.ID
		out = append(out, step)
	}
	return out
}

// ToEvent builds the protobuf event the backend would receive, so the
// judge sees exactly what it sees in production.
func (c Case) ToEvent() *pb.PairedEvent {
	ev := &pb.PairedEvent{
		EventId:      c.ID,
		SessionId:    c.conversationID(),
		InvocationId: c.conversationID(),
		Agent: &pb.AgentContext{
			AgentId:         "eval-agent",
			SystemPrompt:    c.AgentSystemPrompt,
			UserInstruction: c.UserInstruction,
		},
	}
	if c.Kind == "tool" {
		ev.PairType = pb.PairType_PAIR_TYPE_TOOL
		ev.Data = &pb.PairedEvent_Tool{Tool: &pb.ToolPairData{
			ToolName:   c.ToolName,
			ToolCallId: c.ID,
			Input:      c.Input,
			Output:     c.Output,
		}}
		return ev
	}
	llm := &pb.LlmPairData{Output: c.Response, Reasoning: c.Reasoning}
	for _, tc := range c.ToolCalls {
		llm.ToolCalls = append(llm.ToolCalls, &pb.ToolCall{Name: tc.Name, Args: tc.Args})
	}
	ev.PairType = pb.PairType_PAIR_TYPE_LLM
	ev.Data = &pb.PairedEvent_Llm{Llm: llm}
	return ev
}

// conversationID is the sliding-window key for this case. Each case is
// its own conversation unless it is a step of a multi-step case, so
// cases never see each other's history.
func (c Case) conversationID() string {
	if c.conversation != "" {
		return c.conversation
	}
	return c.ID
}
