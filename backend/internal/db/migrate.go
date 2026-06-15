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
		reconciled, appliedRecovery, err := reconcileMigration002(db, name)
		if err != nil {
			return nil, err
		}
		if reconciled {
			if appliedRecovery {
				applied = append(applied, name)
			}
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

func reconcileMigration002(db *sql.DB, name string) (bool, bool, error) {
	if name != "002_verdict_status_policy.sql" {
		return false, false, nil
	}

	hasPolicyColumn, err := tableHasColumn(db, "policies", "fail_closed_on_classifier_error")
	if err != nil {
		return false, false, err
	}
	hasVerdictStatus, err := tableHasColumn(db, "verdicts", "verdict_status")
	if err != nil {
		return false, false, err
	}
	allowsErrorClassification, err := tableSQLContains(db, "verdicts", "'error'")
	if err != nil {
		return false, false, err
	}

	if hasPolicyColumn && hasVerdictStatus && allowsErrorClassification {
		if _, err := db.Exec(`INSERT OR IGNORE INTO schema_migrations (name) VALUES (?)`, name); err != nil {
			return false, false, fmt.Errorf("record recovered %s: %w", name, err)
		}
		return true, false, nil
	}

	if hasPolicyColumn {
		if _, err := db.Exec(migration002VerdictsRecoverySQL); err != nil {
			_, _ = db.Exec("ROLLBACK")
			_, _ = db.Exec("PRAGMA foreign_keys=ON")
			return false, false, fmt.Errorf("recover %s verdicts: %w", name, err)
		}
		if _, err := db.Exec(`INSERT INTO schema_migrations (name) VALUES (?)`, name); err != nil {
			return false, false, fmt.Errorf("record recovered %s: %w", name, err)
		}
		return true, true, nil
	}

	if hasVerdictStatus && allowsErrorClassification {
		if _, err := db.Exec(migration002PolicyColumnSQL); err != nil {
			return false, false, fmt.Errorf("recover %s policy column: %w", name, err)
		}
		if _, err := db.Exec(`INSERT INTO schema_migrations (name) VALUES (?)`, name); err != nil {
			return false, false, fmt.Errorf("record recovered %s: %w", name, err)
		}
		return true, true, nil
	}
	return false, false, nil
}

func tableHasColumn(db *sql.DB, table, column string) (bool, error) {
	rows, err := db.Query(`SELECT name FROM pragma_table_info(?)`, table)
	if err != nil {
		return false, fmt.Errorf("inspect %s columns: %w", table, err)
	}
	defer rows.Close()

	for rows.Next() {
		var name string
		if err := rows.Scan(&name); err != nil {
			return false, err
		}
		if name == column {
			return true, nil
		}
	}
	return false, rows.Err()
}

func tableSQLContains(db *sql.DB, table, needle string) (bool, error) {
	var sqlText string
	err := db.QueryRow(`SELECT sql FROM sqlite_master WHERE type = 'table' AND name = ?`, table).Scan(&sqlText)
	if err == sql.ErrNoRows {
		return false, nil
	}
	if err != nil {
		return false, fmt.Errorf("inspect %s schema: %w", table, err)
	}
	return strings.Contains(sqlText, needle), nil
}

const migration002PolicyColumnSQL = `
ALTER TABLE policies
    ADD COLUMN fail_closed_on_classifier_error INTEGER NOT NULL DEFAULT 0
        CHECK (fail_closed_on_classifier_error IN (0,1));
`

const migration002VerdictsRecoverySQL = `
PRAGMA foreign_keys=OFF;

BEGIN;

DROP TABLE IF EXISTS verdicts_new;

CREATE TABLE verdicts_new (
    id               TEXT    PRIMARY KEY,
    event_id         TEXT    NOT NULL REFERENCES events(id) ON DELETE CASCADE,
    session_id       TEXT    NOT NULL,
    agent_profile_id TEXT             REFERENCES agent_profiles(id) ON DELETE SET NULL,
    mad_code         TEXT    NOT NULL,
    classification   TEXT    NOT NULL CHECK (classification IN ('benign','notify','block','error')),
    verdict_status   TEXT    NOT NULL DEFAULT 'ok'
                                CHECK (verdict_status IN ('ok','error')),
    reasoning        TEXT,
    latency_ms       INTEGER,
    tokens_used      INTEGER NOT NULL DEFAULT 0,
    created_at       TEXT    NOT NULL DEFAULT (strftime('%Y-%m-%dT%H:%M:%fZ','now'))
);

INSERT INTO verdicts_new (
    id,
    event_id,
    session_id,
    agent_profile_id,
    mad_code,
    classification,
    verdict_status,
    reasoning,
    latency_ms,
    tokens_used,
    created_at
)
SELECT
    id,
    event_id,
    session_id,
    agent_profile_id,
    mad_code,
    classification,
    'ok',
    reasoning,
    latency_ms,
    tokens_used,
    created_at
FROM verdicts;

DROP TABLE verdicts;
ALTER TABLE verdicts_new RENAME TO verdicts;

CREATE INDEX IF NOT EXISTS idx_verdicts_event_id   ON verdicts(event_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_session_id ON verdicts(session_id);
CREATE INDEX IF NOT EXISTS idx_verdicts_created_at ON verdicts(created_at);

COMMIT;

PRAGMA foreign_key_check;
PRAGMA foreign_keys=ON;
`
