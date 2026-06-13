-- ============================================================
-- Issue #46: verdict status + classifier-error policy toggle
-- ============================================================
-- adrian: no-transaction
--
-- Rebuild verdicts so the classification CHECK can admit the
-- classifier-error state. The Go/Python runners execute this file
-- outside their own transaction wrapper so foreign_keys can be
-- disabled before this migration's explicit transaction begins.
-- ============================================================

PRAGMA foreign_keys=OFF;

BEGIN;

ALTER TABLE policies
    ADD COLUMN fail_closed_on_classifier_error INTEGER NOT NULL DEFAULT 0
        CHECK (fail_closed_on_classifier_error IN (0,1));

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
