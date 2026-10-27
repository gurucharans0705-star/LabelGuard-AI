"""SQLite schema and versioned migrations.

The database version is kept in SQLite's `PRAGMA user_version`. `migrate()` applies every step
newer than the file's version, in order, so a database created by Day 1 or Day 2 is upgraded in
place and a current one is left alone. A step never drops or rewrites a user's data, except that
Day 3 renames the two original review statuses (open -> pending, acknowledged -> under_review).

Versions
  1  Day 1/2 baseline: analysis_runs, finding_reviews, review_events
  2  Day 3: job ids, extracted fields, findings, audit log (append-only), product catalog,
     change-impact results, AI explanations
"""
from __future__ import annotations

import sqlite3

SCHEMA_VERSION = 2

_V1 = """
CREATE TABLE IF NOT EXISTS analysis_runs (
    id                  INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at          TEXT    NOT NULL,
    approved_filename   TEXT    NOT NULL,
    approved_sha256     TEXT    NOT NULL,
    revised_filename    TEXT    NOT NULL,
    revised_sha256      TEXT    NOT NULL,
    fields_matching     INTEGER NOT NULL,
    fields_changed      INTEGER NOT NULL,
    fields_undetected   INTEGER NOT NULL,
    text_modified       INTEGER NOT NULL,
    text_added          INTEGER NOT NULL,
    text_removed        INTEGER NOT NULL,
    ocr_pages           INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS finding_reviews (
    approved_sha256 TEXT NOT NULL,
    revised_sha256  TEXT NOT NULL,
    finding_id      TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    severity        TEXT NOT NULL,
    status          TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    updated_at      TEXT NOT NULL,
    PRIMARY KEY (approved_sha256, revised_sha256, finding_id)
);
CREATE TABLE IF NOT EXISTS review_events (
    id              INTEGER PRIMARY KEY AUTOINCREMENT,
    approved_sha256 TEXT NOT NULL,
    revised_sha256  TEXT NOT NULL,
    finding_id      TEXT NOT NULL,
    rule_id         TEXT NOT NULL,
    from_status     TEXT NOT NULL,
    to_status       TEXT NOT NULL,
    note            TEXT NOT NULL DEFAULT '',
    at              TEXT NOT NULL
);
"""

# Columns added to analysis_runs during Day 2, for databases made before that.
_V1_RUN_COLUMNS = {
    "findings_flagged": "INTEGER NOT NULL DEFAULT 0",
    "findings_critical": "INTEGER NOT NULL DEFAULT 0",
    "findings_not_evaluated": "INTEGER NOT NULL DEFAULT 0",
}

_V2_TABLES = """
CREATE TABLE IF NOT EXISTS analysis_findings (
    job_id             INTEGER NOT NULL REFERENCES analysis_runs(id),
    finding_id         TEXT    NOT NULL,
    rule_id            TEXT    NOT NULL,
    rule_name          TEXT    NOT NULL,
    category           TEXT    NOT NULL,
    severity           TEXT    NOT NULL,
    result             TEXT    NOT NULL,
    title              TEXT    NOT NULL,
    description        TEXT    NOT NULL,
    field              TEXT    NOT NULL,
    approved_value     TEXT,
    revised_value      TEXT,
    evidence_json      TEXT    NOT NULL DEFAULT '[]',
    recommended_action TEXT    NOT NULL DEFAULT '',
    PRIMARY KEY (job_id, finding_id)
);
CREATE TABLE IF NOT EXISTS extracted_fields (
    job_id    INTEGER NOT NULL REFERENCES analysis_runs(id),
    role      TEXT    NOT NULL,            -- approved | revised
    field_key TEXT    NOT NULL,
    position  INTEGER NOT NULL,            -- order among the values of a multi-valued field
    value     TEXT    NOT NULL,
    page      INTEGER,
    method    TEXT,
    PRIMARY KEY (job_id, role, field_key, position)
);
CREATE TABLE IF NOT EXISTS audit_events (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    event_uid    TEXT    NOT NULL UNIQUE,
    at           TEXT    NOT NULL,
    event_type   TEXT    NOT NULL,
    entity_type  TEXT    NOT NULL,
    entity_id    TEXT    NOT NULL,
    actor        TEXT    NOT NULL,
    summary      TEXT    NOT NULL,
    details_json TEXT    NOT NULL DEFAULT '{}',
    job_uid      TEXT,
    is_demo      INTEGER NOT NULL DEFAULT 0
);
CREATE TRIGGER IF NOT EXISTS audit_events_no_update BEFORE UPDATE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;
CREATE TRIGGER IF NOT EXISTS audit_events_no_delete BEFORE DELETE ON audit_events
BEGIN SELECT RAISE(ABORT, 'audit events are append-only'); END;

CREATE TABLE IF NOT EXISTS markets (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS languages (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS packaging_levels (
    code TEXT PRIMARY KEY, name TEXT NOT NULL, rank INTEGER NOT NULL, is_demo INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS products (
    product_id      TEXT PRIMARY KEY,
    name            TEXT NOT NULL,
    category        TEXT NOT NULL DEFAULT '',
    source_language TEXT NOT NULL DEFAULT 'en',
    is_demo         INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS product_configurations (
    config_id  TEXT PRIMARY KEY,
    product_id TEXT NOT NULL REFERENCES products(product_id),
    name       TEXT NOT NULL,
    is_demo    INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS config_parameters (
    config_id     TEXT NOT NULL REFERENCES product_configurations(config_id),
    parameter_key TEXT NOT NULL,
    value         TEXT NOT NULL,
    PRIMARY KEY (config_id, parameter_key)
);
CREATE TABLE IF NOT EXISTS product_relationships (
    product_id         TEXT NOT NULL REFERENCES products(product_id),
    related_product_id TEXT NOT NULL REFERENCES products(product_id),
    kind               TEXT NOT NULL,        -- e.g. contains_component: product_id contains related_product_id
    note               TEXT NOT NULL DEFAULT '',
    PRIMARY KEY (product_id, related_product_id, kind)
);
CREATE TABLE IF NOT EXISTS label_versions (
    label_id             TEXT PRIMARY KEY,
    label_code           TEXT NOT NULL,
    version              INTEGER NOT NULL,
    status               TEXT NOT NULL,       -- draft | in_review | approved | retired
    product_id           TEXT NOT NULL REFERENCES products(product_id),
    config_id            TEXT NOT NULL REFERENCES product_configurations(config_id),
    market_code          TEXT NOT NULL REFERENCES markets(code),
    language_code        TEXT NOT NULL REFERENCES languages(code),
    packaging_level_code TEXT NOT NULL REFERENCES packaging_levels(code),
    document_sha256      TEXT,
    is_demo              INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS label_required_fields (
    label_id  TEXT NOT NULL REFERENCES label_versions(label_id),
    field_key TEXT NOT NULL,
    PRIMARY KEY (label_id, field_key)
);
CREATE TABLE IF NOT EXISTS label_review_records (
    record_id   TEXT PRIMARY KEY,
    label_id    TEXT NOT NULL REFERENCES label_versions(label_id),
    reviewed_at TEXT NOT NULL,
    reviewer    TEXT NOT NULL,
    outcome     TEXT NOT NULL,
    summary     TEXT NOT NULL DEFAULT '',
    is_demo     INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS impact_runs (
    run_uid       TEXT PRIMARY KEY,
    created_at    TEXT NOT NULL,
    product_id    TEXT NOT NULL,
    config_id     TEXT NOT NULL,
    parameter_key TEXT NOT NULL,
    old_value     TEXT,
    new_value     TEXT,
    source        TEXT NOT NULL,              -- manual | analysis
    job_uid       TEXT,
    item_count    INTEGER NOT NULL DEFAULT 0,
    is_demo       INTEGER NOT NULL DEFAULT 0
);
CREATE TABLE IF NOT EXISTS impact_items (
    run_uid        TEXT NOT NULL REFERENCES impact_runs(run_uid),
    label_id       TEXT NOT NULL,
    label_code     TEXT NOT NULL,
    label_version  INTEGER NOT NULL,
    product_id     TEXT NOT NULL,
    config_id      TEXT NOT NULL,
    market_code    TEXT NOT NULL,
    language_code  TEXT NOT NULL,
    packaging_code TEXT NOT NULL,
    rule_id        TEXT NOT NULL,
    severity       TEXT NOT NULL,
    relationship   TEXT NOT NULL,
    reason         TEXT NOT NULL,
    action         TEXT NOT NULL,
    PRIMARY KEY (run_uid, label_id, rule_id)
);
CREATE TABLE IF NOT EXISTS ai_explanations (
    explanation_uid TEXT PRIMARY KEY,
    created_at      TEXT NOT NULL,
    job_id          INTEGER NOT NULL REFERENCES analysis_runs(id),
    source          TEXT NOT NULL,            -- ai | deterministic
    provider        TEXT NOT NULL DEFAULT '',
    model           TEXT NOT NULL DEFAULT '',
    note            TEXT NOT NULL DEFAULT '',
    content_json    TEXT NOT NULL,
    is_demo         INTEGER NOT NULL DEFAULT 0
);
CREATE INDEX IF NOT EXISTS idx_findings_job ON analysis_findings(job_id);
CREATE INDEX IF NOT EXISTS idx_fields_job ON extracted_fields(job_id);
CREATE INDEX IF NOT EXISTS idx_audit_type ON audit_events(event_type);
CREATE INDEX IF NOT EXISTS idx_audit_job ON audit_events(job_uid);
CREATE INDEX IF NOT EXISTS idx_labels_config ON label_versions(config_id);
CREATE INDEX IF NOT EXISTS idx_labels_product ON label_versions(product_id);
CREATE INDEX IF NOT EXISTS idx_runs_pair ON analysis_runs(approved_sha256, revised_sha256);
"""

# Day 3 columns on tables that already existed.
_V2_COLUMNS = {
    "analysis_runs": {
        "job_uid": "TEXT",
        "is_demo": "INTEGER NOT NULL DEFAULT 0",
        "label_id": "TEXT",
    },
    "finding_reviews": {"reviewer": "TEXT NOT NULL DEFAULT ''"},
    "review_events": {"reviewer": "TEXT NOT NULL DEFAULT ''"},
}


def _columns(connection: sqlite3.Connection, table: str) -> set[str]:
    return {row[1] for row in connection.execute(f"PRAGMA table_info({table})")}


def _add_missing_columns(connection: sqlite3.Connection, table: str, wanted: dict[str, str]) -> None:
    existing = _columns(connection, table)
    for column, definition in wanted.items():
        if column not in existing:
            connection.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _migrate_to_1(connection: sqlite3.Connection) -> None:
    connection.executescript(_V1)
    _add_missing_columns(connection, "analysis_runs", _V1_RUN_COLUMNS)


def _migrate_to_2(connection: sqlite3.Connection) -> None:
    for table, columns in _V2_COLUMNS.items():
        _add_missing_columns(connection, table, columns)
    connection.executescript(_V2_TABLES)
    # Rename the two original review statuses.
    for table, columns in (("finding_reviews", ("status",)), ("review_events", ("from_status", "to_status"))):
        for column in columns:
            connection.execute(f"UPDATE {table} SET {column} = 'pending' WHERE {column} = 'open'")
            connection.execute(f"UPDATE {table} SET {column} = 'under_review' WHERE {column} = 'acknowledged'")
    # Runs saved before job ids existed get a recognisable, unique one.
    connection.execute("UPDATE analysis_runs SET job_uid = 'JOB-LEGACY-' || id WHERE job_uid IS NULL")
    connection.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_runs_job_uid ON analysis_runs(job_uid)")


_STEPS = {1: _migrate_to_1, 2: _migrate_to_2}


def migrate(connection: sqlite3.Connection) -> int:
    """Bring the database up to SCHEMA_VERSION. Returns the version it ends at."""
    version = connection.execute("PRAGMA user_version").fetchone()[0]
    for target in range(version + 1, SCHEMA_VERSION + 1):
        _STEPS[target](connection)
        connection.execute(f"PRAGMA user_version = {int(target)}")
        connection.commit()
    return max(version, SCHEMA_VERSION)
