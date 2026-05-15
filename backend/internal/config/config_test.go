// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package config

import "testing"

func TestLoadAllowsStubModeWithoutLLM(t *testing.T) {
	t.Setenv("ADRIAN_SESSION_SECRET", "test-secret")
	t.Setenv("ADRIAN_LLM_URL", "")
	t.Setenv("ADRIAN_LLM_API_KEY", "")
	t.Setenv("ADRIAN_LLM_MODEL", "")
	t.Setenv("ADRIAN_LLM_MODEL_PATH", "")

	cfg, err := Load()
	if err != nil {
		t.Fatalf("Load() error = %v, want nil", err)
	}
	if cfg.LLMURL != "" {
		t.Fatalf("LLMURL = %q, want empty for stub mode", cfg.LLMURL)
	}
}

func TestLoadRejectsPartialLLMConfig(t *testing.T) {
	t.Setenv("ADRIAN_SESSION_SECRET", "test-secret")
	t.Setenv("ADRIAN_LLM_URL", "http://example.test/v1/chat/completions")
	t.Setenv("ADRIAN_LLM_API_KEY", "")
	t.Setenv("ADRIAN_LLM_MODEL", "local")
	t.Setenv("ADRIAN_LLM_MODEL_PATH", "/models/model.gguf")

	_, err := Load()
	if err == nil {
		t.Fatal("Load() error = nil, want validation failure")
	}
}
