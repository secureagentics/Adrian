// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package store

import (
	"context"
	"database/sql"
	"testing"
	"time"

	_ "modernc.org/sqlite"
)

func TestListEventsNormalisesShortEventTypeFilters(t *testing.T) {
	db := openStoreTestDB(t)
	st := New(db)

	if _, err := db.Exec(`
		INSERT INTO events (id, session_id, event_type, payload, created_at)
		VALUES ('e1', 's1', 'EVENT_TYPE_LLM', '{}', '2026-05-14T10:00:00Z')
	`); err != nil {
		t.Fatalf("insert event: %v", err)
	}

	filters := EventFilters{
		Since:     time.Date(2026, 5, 14, 0, 0, 0, 0, time.UTC),
		EventType: "llm",
	}
	rows, total, err := st.ListEvents(context.Background(), filters, 20, 0)
	if err != nil {
		t.Fatalf("ListEvents: %v", err)
	}
	if total != 1 {
		t.Fatalf("total = %d, want 1", total)
	}
	if len(rows) != 1 {
		t.Fatalf("len(rows) = %d, want 1", len(rows))
	}
	if rows[0].EventType != "EVENT_TYPE_LLM" {
		t.Fatalf("event_type = %q, want EVENT_TYPE_LLM", rows[0].EventType)
	}
}

func TestNormaliseEventTypeFilter(t *testing.T) {
	tests := map[string]string{
		"llm":             "EVENT_TYPE_LLM",
		"LLM":             "EVENT_TYPE_LLM",
		"EVENT_TYPE_LLM":  "EVENT_TYPE_LLM",
		"tool":            "EVENT_TYPE_TOOL",
		"TOOL":            "EVENT_TYPE_TOOL",
		"EVENT_TYPE_TOOL": "EVENT_TYPE_TOOL",
		"custom":          "custom",
	}

	for in, want := range tests {
		if got := normaliseEventTypeFilter(in); got != want {
			t.Fatalf("normaliseEventTypeFilter(%q) = %q, want %q", in, got, want)
		}
	}
}

func openStoreTestDB(t *testing.T) *sql.DB {
	t.Helper()

	db, err := sql.Open("sqlite", "file:storetest?mode=memory&cache=shared")
	if err != nil {
		t.Fatalf("open: %v", err)
	}
	t.Cleanup(func() { _ = db.Close() })

	for _, pragma := range []string{
		"PRAGMA foreign_keys=ON",
		"PRAGMA journal_mode=WAL",
	} {
		if _, err := db.Exec(pragma); err != nil {
			t.Fatalf("apply %q: %v", pragma, err)
		}
	}

	if _, err := db.Exec(`
		CREATE TABLE events (
			id TEXT PRIMARY KEY,
			session_id TEXT NOT NULL,
			agent_id TEXT,
			agent_profile_id TEXT,
			event_type TEXT NOT NULL,
			run_id TEXT,
			payload TEXT NOT NULL,
			tokens_used INTEGER NOT NULL DEFAULT 0,
			created_at TEXT NOT NULL
		);
		CREATE TABLE agent_profiles (
			id TEXT PRIMARY KEY,
			name TEXT NOT NULL UNIQUE,
			enabled INTEGER NOT NULL DEFAULT 0,
			remit TEXT NOT NULL DEFAULT '',
			m0_entries TEXT NOT NULL DEFAULT '[]',
			m3_entries TEXT NOT NULL DEFAULT '[]',
			created_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now')),
			updated_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
		);
		CREATE TABLE verdicts (
			id TEXT PRIMARY KEY,
			event_id TEXT NOT NULL,
			session_id TEXT NOT NULL,
			agent_profile_id TEXT,
			mad_code TEXT NOT NULL,
			classification TEXT NOT NULL,
			reasoning TEXT,
			latency_ms INTEGER,
			tokens_used INTEGER NOT NULL DEFAULT 0,
			created_at TEXT NOT NULL
		);
	`); err != nil {
		t.Fatalf("apply schema: %v", err)
	}

	return db
}
