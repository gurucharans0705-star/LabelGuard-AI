"""SQLite log of label analysis runs.

Day 1 scope: remember *that* two label files were compared and what the counts
were, so the dashboard can show recent runs. Deliberately NOT stored: label text,
extracted fields or the PDFs themselves. Each file is identified by name and
SHA-256 fingerprint only. Day 3 extends this with label versions, findings and
review history.
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
)
"""


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


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database (creating the file, folder and table if needed)."""
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    connection.execute(_SCHEMA)
    return connection


def save_run(result: "AnalysisResult", db_path: Path | str | None = None) -> int:
    """Record an analysis run and return its id. Raises sqlite3.Error if the database is unusable."""
    comparison = result.comparison
    approved, revised = result.approved.document, result.revised.document
    values = (
        datetime.now(timezone.utc).isoformat(timespec="seconds"),
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
    )
    with closing(connect(db_path)) as connection, connection:  # `with connection` commits or rolls back
        cursor = connection.execute(
            "INSERT INTO analysis_runs (created_at, approved_filename, approved_sha256, revised_filename, "
            "revised_sha256, fields_matching, fields_changed, fields_undetected, text_modified, text_added, "
            "text_removed, ocr_pages) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
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
