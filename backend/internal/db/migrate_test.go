// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package db

import (
	"database/sql"
	"testing"
	"testing/fstest"

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
