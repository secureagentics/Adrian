// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package engine

import (
	"fmt"
	"strings"

	pb "github.com/secureagentics/Adrian/backend/internal/proto"
)

// extractTrace renders the user-content text the classifier sees for a
// paired event. Every untrusted interpolation, agent prompts, parent
// prompts, tool args / input / output, LLM output, is wrapped under
// the per-conversation guid so the model treats it as data, not as
// commands. Trusted scaffolding (`Tool Invocation:`, `Chain of Thought:`,
// labels, separators) stays raw.
//
// Empty fields (e.g. SDK omits agent.system_prompt) skip the line
// entirely instead of emitting an empty `<adrian-untrusted ..></..>`
// pair, so the rendered prompt stays clean.
//
// The parent block is omitted for top-level agents (parent absent or
// parent.agent_id empty); only immediate parent context renders.
func extractTrace(ev *pb.PairedEvent, guid string) string {
	var b strings.Builder

	agentSys, agentUser := agentPrompts(ev.GetAgent())
	writeLabelled(&b, "Agent system prompt: ", agentSys, guid)
	writeLabelled(&b, "User instruction: ", agentUser, guid)

	if parent := ev.GetParent(); parent != nil && parent.GetAgentId() != "" {
		parentSys, parentUser := agentPrompts(parent)
		writeLabelled(&b, "Parent system prompt: ", parentSys, guid)
		writeLabelled(&b, "Parent user instruction: ", parentUser, guid)
	}

	b.WriteString("\nClassify this agent trace:\n\n")

	switch ev.PairType {
	case pb.PairType_PAIR_TYPE_LLM:
		writeLLMSection(&b, ev.GetLlm(), guid)
	case pb.PairType_PAIR_TYPE_TOOL:
		writeToolSection(&b, ev.GetTool(), guid)
	}

	return strings.TrimRight(b.String(), "\n")
}

// writeLabelled emits "label<wrapped value>\n" only when value is
// non-empty. Empty values produce no line at all.
func writeLabelled(b *strings.Builder, label, value, guid string) {
	if value == "" {
		return
	}
	fmt.Fprintf(b, "%s%s\n", label, wrap(value, guid))
}

func agentPrompts(a *pb.AgentContext) (string, string) {
	if a == nil {
		return "", ""
	}
	return a.SystemPrompt, a.UserInstruction
}

func writeLLMSection(b *strings.Builder, llm *pb.LlmPairData, guid string) {
	if llm == nil {
		return
	}
	// Clients that send reasoning put it here; older ones send only output,
	// which has always been rendered under this label.
	cot := llm.Reasoning
	if cot == "" {
		cot = llm.Output
	}
	writeLabelled(b, "Chain of Thought: ", cot, guid)
	if llm.Reasoning != "" {
		writeLabelled(b, "Response: ", llm.Output, guid)
	}
	if len(llm.ToolCalls) == 0 {
		return
	}
	if cot != "" {
		b.WriteString("\n")
	}
	b.WriteString("Tool Calls:\n")
	for _, tc := range llm.ToolCalls {
		name := tc.GetName()
		if name == "" {
			name = "?"
		}
		// Tool name is partly user-controllable (an attacker could
		// emit an injected tool call) so wrap the whole call line.
		call := fmt.Sprintf("%s(%s)", name, tc.GetArgs())
		fmt.Fprintf(b, " - %s\n", wrap(call, guid))
	}
}

func writeToolSection(b *strings.Builder, tool *pb.ToolPairData, guid string) {
	if tool == nil {
		return
	}
	name := tool.GetToolName()
	if name == "" {
		name = "?"
	}
	b.WriteString("Tool Invocation:\n")
	fmt.Fprintf(b, "Tool: %s\n", wrap(name, guid))
	// Input / Output may be legitimately empty (a no-op tool call);
	// omit the line entirely instead of emitting an empty wrap.
	writeLabelled(b, "Input: ", tool.GetInput(), guid)
	writeLabelled(b, "Output: ", tool.GetOutput(), guid)
}
