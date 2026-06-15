// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package db

import (
	"database/sql"
	"testing"
	"testing/fstest"

	"github.com/secureagentics/Adrian/backend/migrations"

	_ "modernc.org/sqlite"
)

func TestApplyMigrationsUsesLedger(t *testing.T) {
	conn := openTestDB(t)
	defer conn.Close()

	fsys := fstest.MapFS{
		"001_create.sql": {
			Data: []byte(`CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);`),
		},
		"002_insert.sql": {
			Data: []byte(`INSERT INTO widgets (name) VALUES ('first');`),
		},
	}

	applied, err := applyMigrations(conn, fsys)
	if err != nil {
		t.Fatalf("first applyMigrations: %v", err)
	}
	if got, want := len(applied), 2; got != want {
		t.Fatalf("first applied len = %d, want %d (%v)", got, want, applied)
	}

	applied, err = applyMigrations(conn, fsys)
	if err != nil {
		t.Fatalf("second applyMigrations: %v", err)
	}
	if got := len(applied); got != 0 {
		t.Fatalf("second applied len = %d, want 0 (%v)", got, applied)
	}

	var widgets int
	if err := conn.QueryRow(`SELECT count(*) FROM widgets`).Scan(&widgets); err != nil {
		t.Fatalf("count widgets: %v", err)
	}
	if widgets != 1 {
		t.Fatalf("widgets count = %d, want 1", widgets)
	}

	var ledgerRows int
	if err := conn.QueryRow(`SELECT count(*) FROM schema_migrations`).Scan(&ledgerRows); err != nil {
		t.Fatalf("count schema_migrations: %v", err)
	}
	if ledgerRows != 2 {
		t.Fatalf("schema_migrations count = %d, want 2", ledgerRows)
	}
}

func TestApplyMigrationsDoesNotRecordFailedMigration(t *testing.T) {
	conn := openTestDB(t)
	defer conn.Close()

	fsys := fstest.MapFS{
		"001_create.sql": {
			Data: []byte(`CREATE TABLE widgets (id INTEGER PRIMARY KEY);`),
		},
		"002_bad.sql": {
			Data: []byte(`INSERT INTO missing_table (id) VALUES (1);`),
		},
	}

	applied, err := applyMigrations(conn, fsys)
	if err == nil {
		t.Fatal("applyMigrations unexpectedly succeeded")
	}
	if got, want := len(applied), 0; got != want {
		t.Fatalf("applied len after failure = %d, want %d (%v)", got, want, applied)
	}

	if migrationWasRecorded(t, conn, "002_bad.sql") {
		t.Fatal("failed migration was recorded in schema_migrations")
	}
	if !migrationWasRecorded(t, conn, "001_create.sql") {
		t.Fatal("successful prior migration was not recorded")
	}
}

func TestApplyMigrationsSupportsNoTransactionMarker(t *testing.T) {
	conn := openTestDB(t)
	defer conn.Close()

	fsys := fstest.MapFS{
		"001_no_tx.sql": {
			Data: []byte(noTransactionMarker + `
BEGIN;
CREATE TABLE widgets (id INTEGER PRIMARY KEY, name TEXT NOT NULL);
INSERT INTO widgets (name) VALUES ('marker');
COMMIT;`),
		},
	}

	applied, err := applyMigrations(conn, fsys)
	if err != nil {
		t.Fatalf("applyMigrations: %v", err)
	}
	if got, want := len(applied), 1; got != want {
		t.Fatalf("applied len = %d, want %d (%v)", got, want, applied)
	}
	if !migrationWasRecorded(t, conn, "001_no_tx.sql") {
		t.Fatal("no-transaction migration was not recorded")
	}
}

func TestEmbeddedMigration002UpgradesPopulatedDB(t *testing.T) {
	conn := openTestDB(t)
	defer conn.Close()

	initialSQL, err := migrations.Files.ReadFile("001_initial_schema.sql")
	if err != nil {
		t.Fatalf("read 001 migration: %v", err)
	}
	applied, err := applyMigrations(conn, fstest.MapFS{
		"001_initial_schema.sql": {Data: initialSQL},
	})
	if err != nil {
		t.Fatalf("apply 001 migration: %v", err)
	}
	if got, want := applied, []string{"001_initial_schema.sql"}; len(got) != len(want) || got[0] != want[0] {
		t.Fatalf("applied initial migrations = %v, want %v", got, want)
	}

	if _, err := conn.Exec(`
INSERT INTO events (id, session_id, event_type, payload)
VALUES ('evt-populated', 'sess-populated', 'llm', '{}');
INSERT INTO verdicts (id, event_id, session_id, mad_code, classification, reasoning)
VALUES ('verdict-populated', 'evt-populated', 'sess-populated', 'M4_a', 'block', 'seed');
INSERT INTO hitl_queue (id, event_id, verdict_id, session_id, mad_code)
VALUES ('review-populated', 'evt-populated', 'verdict-populated', 'sess-populated', 'M4_a');
`); err != nil {
		t.Fatalf("seed populated database: %v", err)
	}

	applied, err = applyMigrations(conn, migrations.Files)
	if err != nil {
		t.Fatalf("apply embedded migrations: %v", err)
	}
	if got, want := applied, []string{"002_verdict_status_policy.sql"}; len(got) != len(want) || got[0] != want[0] {
		t.Fatalf("applied upgrade migrations = %v, want %v", got, want)
	}

	var failClosed int
	if err := conn.QueryRow(`SELECT fail_closed_on_classifier_error FROM policies WHERE id = 1`).Scan(&failClosed); err != nil {
		t.Fatalf("query policy flag: %v", err)
	}
	if failClosed != 0 {
		t.Fatalf("fail_closed_on_classifier_error = %d, want 0", failClosed)
	}

	var madCode, classification, verdictStatus string
	if err := conn.QueryRow(`
SELECT mad_code, classification, verdict_status
FROM verdicts WHERE id = 'verdict-populated'
`).Scan(&madCode, &classification, &verdictStatus); err != nil {
		t.Fatalf("query upgraded verdict: %v", err)
	}
	if madCode != "M4_a" || classification != "block" || verdictStatus != "ok" {
		t.Fatalf("upgraded verdict = (%q, %q, %q), want (M4_a, block, ok)", madCode, classification, verdictStatus)
	}

	if _, err := conn.Exec(`
INSERT INTO verdicts (id, event_id, session_id, mad_code, classification, verdict_status, reasoning)
VALUES ('verdict-error', 'evt-populated', 'sess-populated', '', 'error', 'error', 'classifier failure: test');
`); err != nil {
		t.Fatalf("insert classifier-error verdict after upgrade: %v", err)
	}

	var reviewVerdictID string
	if err := conn.QueryRow(`SELECT verdict_id FROM hitl_queue WHERE id = 'review-populated'`).Scan(&reviewVerdictID); err != nil {
		t.Fatalf("query preserved hitl_queue row: %v", err)
	}
	if reviewVerdictID != "verdict-populated" {
		t.Fatalf("preserved hitl_queue verdict_id = %q, want verdict-populated", reviewVerdictID)
	}

	for _, name := range []string{"idx_verdicts_event_id", "idx_verdicts_session_id", "idx_verdicts_created_at"} {
		var seen int
		if err := conn.QueryRow(`SELECT count(*) FROM sqlite_master WHERE type = 'index' AND name = ?`, name).Scan(&seen); err != nil {
			t.Fatalf("query index %s: %v", name, err)
		}
		if seen != 1 {
			t.Fatalf("index %s count = %d, want 1", name, seen)
		}
	}

	rows, err := conn.Query(`PRAGMA foreign_key_check`)
	if err != nil {
		t.Fatalf("foreign_key_check: %v", err)
	}
	defer rows.Close()
	if rows.Next() {
		t.Fatal("foreign_key_check returned violations after 002 migration")
	}

	applied, err = applyMigrations(conn, migrations.Files)
	if err != nil {
		t.Fatalf("second embedded apply: %v", err)
	}
	if len(applied) != 0 {
		t.Fatalf("second embedded apply = %v, want no migrations", applied)
	}
}

func openTestDB(t *testing.T) *sql.DB {
	t.Helper()
	conn, err := sql.Open("sqlite", "file:migratetest?mode=memory&cache=shared")
	if err != nil {
		t.Fatalf("open sqlite: %v", err)
	}
	return conn
}

func migrationWasRecorded(t *testing.T, conn *sql.DB, name string) bool {
	t.Helper()
	var seen int
	err := conn.QueryRow(`SELECT 1 FROM schema_migrations WHERE name = ?`, name).Scan(&seen)
	if err == sql.ErrNoRows {
		return false
	}
	if err != nil {
		t.Fatalf("lookup migration %s: %v", name, err)
	}
	return true
}
