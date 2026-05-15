// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// Adrian backend entrypoint.
//
// Loads config, opens the SQLite database (running idempotent
// migrations), constructs the API server with the LLM-backed
// classifier, and listens on ADRIAN_BACKEND_PORT until SIGTERM.
package main

import (
	"context"
	"errors"
	"fmt"
	"io"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"github.com/joho/godotenv"

	"github.com/secureagentics/Adrian/backend/internal/api"
	"github.com/secureagentics/Adrian/backend/internal/config"
	"github.com/secureagentics/Adrian/backend/internal/db"
	"github.com/secureagentics/Adrian/backend/internal/engine"
	"github.com/secureagentics/Adrian/backend/internal/notifications"
	"github.com/secureagentics/Adrian/backend/internal/store"
	"github.com/secureagentics/Adrian/backend/internal/ws"
)

const shutdownTimeout = 5 * time.Second

func main() {
	// Compose's healthcheck for the distroless backend image runs the
	// same binary with `healthcheck` as argv[1]: HTTP-GET /readyz on
	// the in-container port and exit 0/1 based on the response. Lets
	// us use one binary, no extra wget / curl in the image, no shell.
	if len(os.Args) > 1 && os.Args[1] == "healthcheck" {
		os.Exit(runHealthcheck())
	}

	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	slog.SetDefault(logger)

	// godotenv is best-effort: the bootstrap container writes .env in
	// the working directory; in compose, env vars arrive directly.
	_ = godotenv.Load(".env")

	cfg, err := config.Load()
	if err != nil {
		slog.Error("config.load_failed", "error", err)
		os.Exit(1)
	}

	conn, err := db.Open(cfg.DBPath)
	if err != nil {
		slog.Error("db.open_failed", "error", err, "path", cfg.DBPath)
		os.Exit(1)
	}
	defer conn.Close()

	st := store.New(conn)

	ctx, stop := signal.NotifyContext(context.Background(),
		syscall.SIGINT, syscall.SIGTERM)
	defer stop()

	// Sliding window: per-(session, invocation, agent_id) ring buffer
	// of recent paired events, plus the per-key mutex that serialises
	// the read-classify-publish-push triple. Sweep evicts cold entries
	// every 5 minutes.
	windowTTL := time.Duration(cfg.SlidingWindowTTL) * time.Second
	window := engine.NewSlidingWindow(engine.WindowOpts{
		Size: cfg.SlidingWindowSize,
		TTL:  windowTTL,
	})
	go window.Sweep(ctx, 5*time.Minute, windowTTL)

	classifier := engine.NewHTTPClient(cfg.LLMURL, cfg.LLMAPIKey, cfg.LLMModel, window, st)
	slog.Info("engine.http_client.wired",
		"url", cfg.LLMURL, "model", cfg.LLMModel,
		"window_size", cfg.SlidingWindowSize,
		"window_ttl_seconds", cfg.SlidingWindowTTL,
	)

	// Discord notification dispatcher: a single goroutine consumes
	// verdicts and fans out to enabled webhooks. The WS handler sends
	// non-blockingly so a slow/unreachable Discord never stalls classify.
	dispatcher := notifications.NewDispatcher(st, cfg.DashboardURL)
	go dispatcher.Run(ctx)
	verdictHook := func(eventID, sessionID, agentID, madCode, classification string) {
		select {
		case dispatcher.Verdicts() <- notifications.VerdictNotification{
			EventID:        eventID,
			SessionID:      sessionID,
			AgentID:        agentID,
			MADCode:        madCode,
			Classification: classification,
		}:
		default:
			slog.Warn("notifications.queue_full_dropping", "event_id", eventID)
		}
	}

	// Hub serialises server-pushed WS frames per session_id and is the
	// channel the REST review approve/reject path uses to ride a HITL
	// resolution back to a connected SDK.
	hub := ws.NewHub()
	// Connection registry tracks live WS sockets per api_key_id so the
	// /api/agent-profiles/.../keys rotate path and /api/keys/{id}
	// revoke path can terminate any in-flight session bound to the
	// revoked key. Without this a leaked key keeps working until the
	// SDK reconnects on its own.
	registry := ws.NewConnRegistry()

	srv := &http.Server{
		Addr:              ":" + cfg.BackendPort,
		Handler:           api.NewServer(cfg, conn, st, classifier, hub, registry, verdictHook),
		ReadHeaderTimeout: 5 * time.Second,
	}

	go func() {
		slog.Info("startup",
			"port", cfg.BackendPort,
			"db_path", cfg.DBPath,
			"llm_url", cfg.LLMURL,
			"llm_model_path", cfg.LLMModelPath,
		)
		if err := srv.ListenAndServe(); err != nil && !errors.Is(err, http.ErrServerClosed) {
			slog.Error("listen_failed", "error", err)
			stop()
		}
	}()

	<-ctx.Done()
	slog.Info("shutdown.begin")

	shutdownCtx, cancel := context.WithTimeout(context.Background(), shutdownTimeout)
	defer cancel()
	if err := srv.Shutdown(shutdownCtx); err != nil {
		slog.Error("shutdown.error", "error", err)
		os.Exit(1)
	}
	slog.Info("shutdown.complete")
}

// runHealthcheck is the entrypoint for `adrian healthcheck`. Used as
// the compose healthcheck command for the distroless backend image:
// HTTP-GET the in-container readiness URL and translate the response
// into a process exit code. 0 = ready (HTTP 200), 1 = not ready or
// any transport error. The port is read from ADRIAN_BACKEND_PORT
// (matching the listener bind in main); compose ensures the same
// value is set on both the listener and the probe.
func runHealthcheck() int {
	port := os.Getenv("ADRIAN_BACKEND_PORT")
	if port == "" {
		port = "8080"
	}
	url := "http://127.0.0.1:" + port + "/readyz"
	client := &http.Client{Timeout: 5 * time.Second}
	resp, err := client.Get(url)
	if err != nil {
		fmt.Fprintf(os.Stderr, "healthcheck: %v\n", err)
		return 1
	}
	defer resp.Body.Close()
	body, _ := io.ReadAll(io.LimitReader(resp.Body, 4096))
	if resp.StatusCode != http.StatusOK {
		fmt.Fprintf(os.Stderr, "healthcheck: status=%d body=%s\n", resp.StatusCode, body)
		return 1
	}
	return 0
}
