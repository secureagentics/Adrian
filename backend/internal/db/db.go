// SPDX-License-Identifier: Apache-2.0
// Copyright (c) 2026 SecureAgentics

// Package db opens the SQLite database, applies pending ledger-tracked
// migrations, and exposes the *sql.DB handle to the rest of the backend.
package db

import (
	"database/sql"
	"fmt"
	"log/slog"

	_ "modernc.org/sqlite"

	"github.com/secureagentics/Adrian/backend/migrations"
)

// Open opens the SQLite database at path, applies the WAL / FK
// pragmas, and runs each pending embedded migration in lexical order.
// Applied migrations are recorded in schema_migrations so startup can
// safely skip files that already ran.
func Open(path string) (*sql.DB, error) {
	conn, err := sql.Open("sqlite", path)
	if err != nil {
		return nil, fmt.Errorf("open sqlite at %s: %w", path, err)
	}

	pragmas := []string{
		"PRAGMA journal_mode=WAL",
		"PRAGMA foreign_keys=ON",
		"PRAGMA synchronous=NORMAL",
		"PRAGMA busy_timeout=5000",
	}
	for _, p := range pragmas {
		if _, err := conn.Exec(p); err != nil {
			conn.Close()
			return nil, fmt.Errorf("apply %q: %w", p, err)
		}
	}

	if err := conn.Ping(); err != nil {
		conn.Close()
		return nil, fmt.Errorf("ping sqlite: %w", err)
	}

	applied, err := applyMigrations(conn, migrations.Files)
	if err != nil {
		conn.Close()
		return nil, fmt.Errorf("apply migrations: %w", err)
	}
	slog.Info("db.opened", "path", path, "migrations_applied", applied)

	return conn, nil
}
