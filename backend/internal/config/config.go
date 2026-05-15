// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// Package config loads the Adrian backend configuration from environment
// variables and validates the result.
//
// The contract matches `.env.example` at the repo root, which is also
// what the adrian-setup bootstrap container writes.
package config

import (
	"errors"
	"fmt"
	"os"
	"strconv"
	"strings"
)

type Config struct {
	BackendPort       string
	DashboardPort     string
	DashboardURL      string
	DBPath            string
	LLMURL            string
	LLMAPIKey         string
	LLMModel          string
	LLMModelPath      string
	LLMCtxSize        int
	SessionSecret     string
	SlidingWindowSize int
	SlidingWindowTTL  int // seconds
}

// Load reads env vars, applies defaults, and returns a validated Config.
// Returns an error if a required value is missing or malformed.
func Load() (*Config, error) {
	cfg := &Config{
		BackendPort:   envOr("ADRIAN_BACKEND_PORT", "8080"),
		DashboardPort: envOr("ADRIAN_DASHBOARD_PORT", "3000"),
		DashboardURL:  strings.TrimRight(envOr("ADRIAN_DASHBOARD_URL", "http://localhost:3000"), "/"),
		DBPath:        envOr("ADRIAN_DB_PATH", "/data/adrian.db"),
		LLMURL:        os.Getenv("ADRIAN_LLM_URL"),
		LLMAPIKey:     os.Getenv("ADRIAN_LLM_API_KEY"),
		LLMModel:      os.Getenv("ADRIAN_LLM_MODEL"),
		LLMModelPath:  os.Getenv("ADRIAN_LLM_MODEL_PATH"),
		SessionSecret: os.Getenv("ADRIAN_SESSION_SECRET"),
	}

	ctxSize, err := strconv.Atoi(envOr("ADRIAN_LLM_CTX_SIZE", "8192"))
	if err != nil {
		return nil, fmt.Errorf("ADRIAN_LLM_CTX_SIZE: %w", err)
	}
	cfg.LLMCtxSize = ctxSize

	windowSize, err := strconv.Atoi(envOr("ADRIAN_SLIDING_WINDOW_SIZE", "16"))
	if err != nil || windowSize <= 0 {
		return nil, fmt.Errorf("ADRIAN_SLIDING_WINDOW_SIZE: must be a positive integer")
	}
	cfg.SlidingWindowSize = windowSize

	windowTTL, err := strconv.Atoi(envOr("ADRIAN_SLIDING_WINDOW_TTL_SECONDS", "86400"))
	if err != nil || windowTTL <= 0 {
		return nil, fmt.Errorf("ADRIAN_SLIDING_WINDOW_TTL_SECONDS: must be a positive integer")
	}
	cfg.SlidingWindowTTL = windowTTL

	if cfg.LLMURL != "" && (cfg.LLMAPIKey == "" || cfg.LLMModel == "" || cfg.LLMModelPath == "") {
		return nil, errors.New(
			"when ADRIAN_LLM_URL is set, ADRIAN_LLM_API_KEY, ADRIAN_LLM_MODEL, and " +
				"ADRIAN_LLM_MODEL_PATH must also be set " +
				"(run `adrian-setup bootstrap --gguf <name>`)",
		)
	}

	if cfg.SessionSecret == "" {
		return nil, errors.New(
			"ADRIAN_SESSION_SECRET must be set (run `adrian-setup bootstrap`)",
		)
	}

	return cfg, nil
}

func envOr(key, fallback string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return fallback
}
