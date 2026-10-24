"""SQLite storage: a log of analysis runs and the reviewer's decisions on findings.

What is stored: file names, SHA-256 fingerprints, counts, finding ids and rule ids, and the
review status and note a person typed. What is deliberately NOT stored: label text, extracted
fields, evidence quotes or the PDFs themselves. A finding is identified by a stable id (a hash
of the rule, field, values and pages), so a decision made on one analysis carries over when the
same two files are analysed again.
"""
from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import TYPE_CHECKING

from labelguard import config

if TYPE_CHECKING:  # avoid an import cycle at run time
    from labelguard.pipeline import AnalysisResult
    from labelguard.rules.models import VerificationReport

REVIEW_STATUSES: tuple[str, ...] = ("open", "acknowledged", "resolved")
REVIEW_STATUS_LABELS = {"open": "Open", "acknowledged": "Acknowledged", "resolved": "Resolved"}

_SCHEMA = """
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

# Columns added after the first release; older database files are upgraded in place.
_RUN_COLUMNS_ADDED_LATER = {
    "findings_flagged": "INTEGER NOT NULL DEFAULT 0",
    "findings_critical": "INTEGER NOT NULL DEFAULT 0",
    "findings_not_evaluated": "INTEGER NOT NULL DEFAULT 0",
}


@dataclass(frozen=True)
class RunRecord:
    """One saved analysis run."""

    id: int
    created_at: str  # UTC, ISO 8601
    approved_filename: str
    approved_sha256: str
    revised_filename: str
    revised_sha256: str
    fields_matching: int
    fields_changed: int
    fields_undetected: int
    text_modified: int
    text_added: int
    text_removed: int
    ocr_pages: int
    findings_flagged: int = 0
    findings_critical: int = 0
    findings_not_evaluated: int = 0


@dataclass(frozen=True)
class ReviewRecord:
    """A reviewer's current decision on one finding."""

    finding_id: str
    rule_id: str
    severity: str
    status: str
    note: str
    updated_at: str


@dataclass(frozen=True)
class ReviewEvent:
    """One entry in a finding's review history."""

    finding_id: str
    rule_id: str
    from_status: str
    to_status: str
    note: str
    at: str


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database (creating the file, folder and tables if needed, upgrading old files)."""
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        connection.executescript(_SCHEMA)
        existing = {row["name"] for row in connection.execute("PRAGMA table_info(analysis_runs)")}
        for column, definition in _RUN_COLUMNS_ADDED_LATER.items():
            if column not in existing:
                connection.execute(f"ALTER TABLE analysis_runs ADD COLUMN {column} {definition}")
        connection.commit()
    except sqlite3.Error:
        connection.close()
        raise
    return connection


# --- Analysis runs ------------------------------------------------------------------------------
def save_run(
    result: "AnalysisResult",
    db_path: Path | str | None = None,
    report: "VerificationReport | None" = None,
) -> int:
    """Record an analysis run and return its id. Raises sqlite3.Error if the database is unusable.

    Pass the verification `report` to also record how many findings the run produced.
    """
    comparison = result.comparison
    approved, revised = result.approved.document, result.revised.document
    flagged = report.flagged if report else ()
    values = (
        _now(),
        approved.filename,
        approved.sha256,
        revised.filename,
        revised.sha256,
        comparison.matching_fields,
        comparison.changed_fields,
        comparison.undetected_fields,
        comparison.count_text("modified"),
        comparison.count_text("added"),
        comparison.count_text("removed"),
        len(approved.ocr_pages) + len(revised.ocr_pages),
        len(flagged),
        sum(1 for finding in flagged if finding.severity == "critical_review"),
        len(report.not_evaluated) if report else 0,
    )
    with closing(connect(db_path)) as connection, connection:  # `with connection` commits or rolls back
        cursor = connection.execute(
            "INSERT INTO analysis_runs (created_at, approved_filename, approved_sha256, revised_filename, "
            "revised_sha256, fields_matching, fields_changed, fields_undetected, text_modified, text_added, "
            "text_removed, ocr_pages, findings_flagged, findings_critical, findings_not_evaluated) "
            "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        return int(cursor.lastrowid)


def list_runs(limit: int = 20, db_path: Path | str | None = None) -> list[RunRecord]:
    """Most recent runs first. Raises sqlite3.Error if the database is unusable."""
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    return [RunRecord(**dict(row)) for row in rows]


# --- Reviews of findings --------------------------------------------------------------------------
def set_review(
    approved_sha256: str,
    revised_sha256: str,
    finding_id: str,
    rule_id: str,
    severity: str,
    status: str,
    note: str = "",
    db_path: Path | str | None = None,
) -> ReviewRecord:
    """Record a reviewer's decision (and add it to the finding's history). Raises ValueError for an unknown status."""
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Unknown review status '{status}'. Use one of: {', '.join(REVIEW_STATUSES)}.")
    note = note.strip()
    now = _now()
    with closing(connect(db_path)) as connection, connection:
        row = connection.execute(
            "SELECT status FROM finding_reviews WHERE approved_sha256 = ? AND revised_sha256 = ? AND finding_id = ?",
            (approved_sha256, revised_sha256, finding_id),
        ).fetchone()
        previous = row["status"] if row else "open"
        connection.execute(
            "INSERT INTO finding_reviews (approved_sha256, revised_sha256, finding_id, rule_id, severity, status, note, "
            "updated_at) VALUES (?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (approved_sha256, revised_sha256, finding_id) DO UPDATE SET "
            "status = excluded.status, note = excluded.note, updated_at = excluded.updated_at, "
            "rule_id = excluded.rule_id, severity = excluded.severity",
            (approved_sha256, revised_sha256, finding_id, rule_id, severity, status, note, now),
        )
        connection.execute(
            "INSERT INTO review_events (approved_sha256, revised_sha256, finding_id, rule_id, from_status, to_status, "
            "note, at) VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
            (approved_sha256, revised_sha256, finding_id, rule_id, previous, status, note, now),
        )
    return ReviewRecord(finding_id, rule_id, severity, status, note, now)


def get_reviews(
    approved_sha256: str, revised_sha256: str, db_path: Path | str | None = None
) -> dict[str, ReviewRecord]:
    """Current decisions for one pair of files, by finding id. Findings never reviewed are absent (they are open)."""
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT finding_id, rule_id, severity, status, note, updated_at FROM finding_reviews "
            "WHERE approved_sha256 = ? AND revised_sha256 = ?",
            (approved_sha256, revised_sha256),
        ).fetchall()
    return {row["finding_id"]: ReviewRecord(**dict(row)) for row in rows}


def review_history(
    approved_sha256: str, revised_sha256: str, finding_id: str | None = None, db_path: Path | str | None = None
) -> list[ReviewEvent]:
    """The decisions made on a pair of files, oldest first (optionally for one finding)."""
    query = (
        "SELECT finding_id, rule_id, from_status, to_status, note, at FROM review_events "
        "WHERE approved_sha256 = ? AND revised_sha256 = ?"
    )
    params: list[str] = [approved_sha256, revised_sha256]
    if finding_id is not None:
        query += " AND finding_id = ?"
        params.append(finding_id)
    with closing(connect(db_path)) as connection:
        rows = connection.execute(query + " ORDER BY id", params).fetchall()
    return [ReviewEvent(**dict(row)) for row in rows]
