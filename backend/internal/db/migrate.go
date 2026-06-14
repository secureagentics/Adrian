// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

package db

import (
	"database/sql"
	"fmt"
	"io/fs"
	"sort"
	"strings"
)

const migrationLedgerDDL = `
CREATE TABLE IF NOT EXISTS schema_migrations (
    name       TEXT PRIMARY KEY,
    applied_at TEXT NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);`

const noTransactionMarker = "-- adrian: no-transaction"

// applyMigrations walks fsys for `*.sql` files and applies each
// previously-unseen migration in lexical order. Applied files are
// recorded in schema_migrations by filename, so future startup runs
// skip them instead of requiring every migration to be idempotent.
// Returns the list of migration files applied during this call.
func applyMigrations(db *sql.DB, fsys fs.FS) ([]string, error) {
	var names []string
	err := fs.WalkDir(fsys, ".", func(path string, d fs.DirEntry, err error) error {
		if err != nil {
			return err
		}
		if d.IsDir() || !strings.HasSuffix(path, ".sql") {
			return nil
		}
		names = append(names, path)
		return nil
	})
	if err != nil {
		return nil, err
	}
	sort.Strings(names)

	if _, err := db.Exec(migrationLedgerDDL); err != nil {
		return nil, fmt.Errorf("ensure schema_migrations: %w", err)
	}

	applied := make([]string, 0, len(names))
	for _, name := range names {
		alreadyApplied, err := migrationApplied(db, name)
		if err != nil {
			return nil, err
		}
		if alreadyApplied {
			continue
		}

		body, err := fs.ReadFile(fsys, name)
		if err != nil {
			return nil, fmt.Errorf("read %s: %w", name, err)
		}
		bodyText := string(body)

		if strings.Contains(bodyText, noTransactionMarker) {
			if _, err := db.Exec(bodyText); err != nil {
				_, _ = db.Exec("ROLLBACK")
				_, _ = db.Exec("PRAGMA foreign_keys=ON")
				return nil, fmt.Errorf("exec %s: %w", name, err)
			}
			if _, err := db.Exec(`INSERT INTO schema_migrations (name) VALUES (?)`, name); err != nil {
				return nil, fmt.Errorf("record %s: %w", name, err)
			}
		} else {
			tx, err := db.Begin()
			if err != nil {
				return nil, fmt.Errorf("begin %s: %w", name, err)
			}
			if _, err := tx.Exec(bodyText); err != nil {
				_ = tx.Rollback()
				return nil, fmt.Errorf("exec %s: %w", name, err)
			}
			if _, err := tx.Exec(`INSERT INTO schema_migrations (name) VALUES (?)`, name); err != nil {
				_ = tx.Rollback()
				return nil, fmt.Errorf("record %s: %w", name, err)
			}
			if err := tx.Commit(); err != nil {
				return nil, fmt.Errorf("commit %s: %w", name, err)
			}
		}
		applied = append(applied, name)
	}
	return applied, nil
}

func migrationApplied(db *sql.DB, name string) (bool, error) {
	var seen int
	err := db.QueryRow(`SELECT 1 FROM schema_migrations WHERE name = ?`, name).Scan(&seen)
	if err == nil {
		return true, nil
	}
	if err == sql.ErrNoRows {
		return false, nil
	}
	return false, fmt.Errorf("lookup migration %s: %w", name, err)
}
