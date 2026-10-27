"""The audit log: an append-only record of what happened, when, and who did it.

Events are written inside the same transaction as the change they describe, so a status change
and its audit event are saved together or not at all. The table has database triggers that refuse
UPDATE and DELETE, so history cannot be edited through this application. That is a safeguard
against mistakes, not tamper-proofing: someone with direct access to the database file could
still change it.

An event stores identifiers, counts, statuses and the short text a reviewer typed. It never stores
label documents.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

ANALYSIS_CREATED = "analysis_created"
REVIEW_STATUS_CHANGED = "review_status_changed"
REPORT_GENERATED = "report_generated"
IMPACT_ANALYSIS_RUN = "impact_analysis_run"
AI_EXPLANATION_CREATED = "ai_explanation_created"
LABEL_LINKED = "label_linked"
DEMO_DATA_LOADED = "demo_data_loaded"
DEMO_DATA_CLEARED = "demo_data_cleared"

EVENT_LABELS = {
    ANALYSIS_CREATED: "Analysis created",
    REVIEW_STATUS_CHANGED: "Review status changed",
    REPORT_GENERATED: "Report generated",
    IMPACT_ANALYSIS_RUN: "Impact analysis run",
    AI_EXPLANATION_CREATED: "Explanation created",
    LABEL_LINKED: "Analysis linked to label record",
    DEMO_DATA_LOADED: "Demo data loaded",
    DEMO_DATA_CLEARED: "Demo data cleared",
}

SYSTEM_ACTOR = "system"  # automated, non-decision events (an analysis was run, a file was downloaded)


@dataclass(frozen=True)
class AuditEvent:
    id: int
    event_uid: str
    at: str  # UTC, ISO 8601
    event_type: str
    entity_type: str
    entity_id: str
    actor: str
    summary: str
    details: dict
    job_uid: str | None
    is_demo: bool

    @property
    def type_label(self) -> str:
        return EVENT_LABELS.get(self.event_type, self.event_type)


def now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def record(
    connection: sqlite3.Connection,
    event_type: str,
    entity_type: str,
    entity_id: str,
    *,
    actor: str = SYSTEM_ACTOR,
    summary: str,
    details: dict | None = None,
    job_uid: str | None = None,
    is_demo: bool = False,
) -> str:
    """Add an event using the caller's connection (and so the caller's transaction). Returns its id."""
    event_uid = f"EVT-{uuid.uuid4().hex[:16]}"
    connection.execute(
        "INSERT INTO audit_events (event_uid, at, event_type, entity_type, entity_id, actor, summary, "
        "details_json, job_uid, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
        (event_uid, now(), event_type, entity_type, entity_id, actor, summary,
         json.dumps(details or {}, sort_keys=True, default=str), job_uid, int(is_demo)),
    )
    return event_uid


def log_event(
    event_type: str,
    entity_type: str,
    entity_id: str,
    *,
    actor: str = SYSTEM_ACTOR,
    summary: str,
    details: dict | None = None,
    job_uid: str | None = None,
    is_demo: bool = False,
    db_path: Path | str | None = None,
) -> str:
    """Add one event in its own transaction. Raises sqlite3.Error if the database is unusable."""
    from labelguard.db.database import connect

    with closing(connect(db_path)) as connection, connection:
        return record(connection, event_type, entity_type, entity_id, actor=actor, summary=summary,
                      details=details, job_uid=job_uid, is_demo=is_demo)


def list_events(
    *,
    event_types: list[str] | None = None,
    job_uid: str | None = None,
    entity_id: str | None = None,
    limit: int = 500,
    db_path: Path | str | None = None,
) -> list[AuditEvent]:
    """Newest first."""
    from labelguard.db.database import connect

    clauses, params = [], []
    if event_types:
        clauses.append(f"event_type IN ({','.join('?' * len(event_types))})")
        params += list(event_types)
    if job_uid:
        clauses.append("job_uid = ?")
        params.append(job_uid)
    if entity_id:
        clauses.append("entity_id = ?")
        params.append(entity_id)
    where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            f"SELECT * FROM audit_events {where} ORDER BY id DESC LIMIT ?", [*params, max(1, int(limit))]
        ).fetchall()
    return [
        AuditEvent(
            id=row["id"], event_uid=row["event_uid"], at=row["at"], event_type=row["event_type"],
            entity_type=row["entity_type"], entity_id=row["entity_id"], actor=row["actor"], summary=row["summary"],
            details=json.loads(row["details_json"] or "{}"), job_uid=row["job_uid"], is_demo=bool(row["is_demo"]),
        )
        for row in rows
    ]
