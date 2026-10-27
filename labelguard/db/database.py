"""SQLite storage: analysis jobs, extracted fields, findings, review decisions and the audit trail.

What is stored for an analysis: file names, SHA-256 fingerprints, counts, the extracted field
values (short strings), each finding with its evidence quotes, and what a reviewer typed (name,
decision, short rationale). What is deliberately NOT stored: the PDFs and the full label text.

A finding has a stable id (a hash of the rule, field, values and pages). A reviewer's decision is
keyed by the pair of files plus that id, so it carries over when the same two files are analysed
again. Resolving a finding never deletes anything: every status change is kept in `review_events`
and in the append-only audit log.

Review statuses (Day 3): pending -> under_review -> resolved | escalated, and any of them can be
reopened to pending. Resolving or escalating needs a short rationale. Only a named person can
change a status; the AI explanation code never touches this module.
"""
from __future__ import annotations

import json
import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from labelguard import config
from labelguard.db import audit
from labelguard.db.audit import now as _now
from labelguard.db.schema import migrate
from labelguard.fields.models import FIELD_LABELS
from labelguard.rules.models import RESULT_FLAGGED, Evidence, Finding

if TYPE_CHECKING:  # avoid an import cycle at run time
    from labelguard.pipeline import AnalysisResult
    from labelguard.rules.models import VerificationReport

REVIEW_STATUSES: tuple[str, ...] = ("pending", "under_review", "escalated", "resolved")
REVIEW_STATUS_LABELS = {
    "pending": "Pending review",
    "under_review": "Under review",
    "escalated": "Escalated",
    "resolved": "Resolved",
}
RATIONALE_REQUIRED = frozenset({"resolved", "escalated"})
MAX_RATIONALE = 500
MAX_VALUE = 500  # longest extracted value or finding quote stored
# Names that must not be recorded as a reviewer: decisions are made by people. This is a guard
# against wiring mistakes (an AI component trying to decide), not a security boundary.
RESERVED_REVIEWERS = frozenset({"ai", "claude", "assistant", "llm", "model", "system", "automated", "bot", "auto"})


@dataclass(frozen=True)
class RunRecord:
    """One saved analysis job."""

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
    job_uid: str = ""
    is_demo: int = 0
    label_id: str | None = None


@dataclass(frozen=True)
class ReviewRecord:
    """A reviewer's current decision on one finding."""

    finding_id: str
    rule_id: str
    severity: str
    status: str
    rationale: str
    updated_at: str
    reviewer: str = ""


@dataclass(frozen=True)
class ReviewEvent:
    """One entry in a finding's review history."""

    finding_id: str
    rule_id: str
    from_status: str
    to_status: str
    rationale: str
    at: str
    reviewer: str = ""


@dataclass(frozen=True)
class QueueItem:
    """A finding from the latest analysis of a pair of files, with its current review state."""

    finding: Finding
    job_id: int
    job_uid: str
    approved_filename: str
    revised_filename: str
    approved_sha256: str
    revised_sha256: str
    created_at: str
    is_demo: bool
    status: str
    rationale: str
    reviewer: str
    updated_at: str | None


def connect(db_path: Path | str | None = None) -> sqlite3.Connection:
    """Open the database (creating the file, folder and tables if needed, upgrading old files)."""
    path = Path(db_path) if db_path is not None else config.DB_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    connection = sqlite3.connect(path)
    try:
        connection.row_factory = sqlite3.Row
        migrate(connection)
    except sqlite3.Error:
        connection.close()
        raise
    return connection


def _cap(text: str | None, limit: int = MAX_VALUE) -> str | None:
    if text is None:
        return None
    text = " ".join(str(text).split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


# --- Analysis jobs ------------------------------------------------------------------------------
def save_run(
    result: "AnalysisResult",
    db_path: Path | str | None = None,
    report: "VerificationReport | None" = None,
    *,
    label_id: str | None = None,
    is_demo: bool = False,
) -> int:
    """Record an analysis job, its extracted fields and findings, and return the job id.

    Everything is written in one transaction together with the 'analysis created' audit event.
    Raises sqlite3.Error if the database is unusable.
    """
    comparison = result.comparison
    approved, revised = result.approved.document, result.revised.document
    flagged = report.flagged if report else ()
    job_uid = f"JOB-{uuid.uuid4().hex[:12]}"
    created = _now()
    values = (
        created, approved.filename, approved.sha256, revised.filename, revised.sha256,
        comparison.matching_fields, comparison.changed_fields, comparison.undetected_fields,
        comparison.count_text("modified"), comparison.count_text("added"), comparison.count_text("removed"),
        len(approved.ocr_pages) + len(revised.ocr_pages),
        len(flagged), sum(1 for f in flagged if f.severity == "critical_review"),
        len(report.not_evaluated) if report else 0,
        job_uid, int(is_demo), label_id,
    )
    with closing(connect(db_path)) as connection, connection:  # `with connection` commits or rolls back
        cursor = connection.execute(
            "INSERT INTO analysis_runs (created_at, approved_filename, approved_sha256, revised_filename, "
            "revised_sha256, fields_matching, fields_changed, fields_undetected, text_modified, text_added, "
            "text_removed, ocr_pages, findings_flagged, findings_critical, findings_not_evaluated, "
            "job_uid, is_demo, label_id) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            values,
        )
        job_id = int(cursor.lastrowid)
        for role, labels in (("approved", result.approved.fields), ("revised", result.revised.fields)):
            for key in FIELD_LABELS:
                for position, hit in enumerate(labels.get(key).hits):
                    connection.execute(
                        "INSERT INTO extracted_fields (job_id, role, field_key, position, value, page, method) "
                        "VALUES (?, ?, ?, ?, ?, ?, ?)",
                        (job_id, role, key, position, _cap(hit.value) or "", hit.page, hit.method),
                    )
        if report:
            for finding in report.findings:
                connection.execute(
                    "INSERT OR REPLACE INTO analysis_findings (job_id, finding_id, rule_id, rule_name, category, "
                    "severity, result, title, description, field, approved_value, revised_value, evidence_json, "
                    "recommended_action) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (job_id, finding.id, finding.rule_id, finding.rule_name, finding.category, finding.severity,
                     finding.result, finding.title, finding.description, finding.field,
                     _cap(finding.approved_value), _cap(finding.revised_value), _evidence_json(finding),
                     finding.recommended_action),
                )
        audit.record(
            connection, audit.ANALYSIS_CREATED, "analysis_job", job_uid, summary=(
                f"Analysis of {approved.filename} vs {revised.filename}: {len(flagged)} flagged, "
                f"{len(report.not_evaluated) if report else 0} not verified"
            ),
            details={"job_id": job_id, "approved_sha256": approved.sha256, "revised_sha256": revised.sha256,
                     "findings": len(report.findings) if report else 0, "label_id": label_id},
            job_uid=job_uid, is_demo=is_demo,
        )
        return job_id


def _evidence_json(finding: Finding) -> str:
    return json.dumps([
        {"source": e.source, "page": e.page, "text": _cap(e.text, 800) or "", "kind": e.kind}
        for e in finding.evidence
    ])


def _finding_from_row(row: sqlite3.Row) -> Finding:
    evidence = tuple(
        Evidence(item["source"], item["page"], item["text"], None, item.get("kind", "text"))
        for item in json.loads(row["evidence_json"] or "[]")
    )
    return Finding(
        id=row["finding_id"], rule_id=row["rule_id"], rule_name=row["rule_name"], category=row["category"],
        severity=row["severity"], result=row["result"], title=row["title"], description=row["description"],
        field=row["field"], approved_value=row["approved_value"], revised_value=row["revised_value"],
        evidence=evidence, recommended_action=row["recommended_action"],
    )


def list_runs(limit: int = 20, db_path: Path | str | None = None) -> list[RunRecord]:
    """Most recent jobs first. Raises sqlite3.Error if the database is unusable."""
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT * FROM analysis_runs ORDER BY id DESC LIMIT ?", (max(1, int(limit)),)
        ).fetchall()
    return [RunRecord(**dict(row)) for row in rows]


def get_run(job_id: int, db_path: Path | str | None = None) -> RunRecord | None:
    with closing(connect(db_path)) as connection:
        row = connection.execute("SELECT * FROM analysis_runs WHERE id = ?", (job_id,)).fetchone()
    return RunRecord(**dict(row)) if row else None


def list_runs_for_labels(label_ids: list[str], db_path: Path | str | None = None) -> list[RunRecord]:
    """Analysis jobs linked to any of the given catalog label records, newest first."""
    if not label_ids:
        return []
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            f"SELECT * FROM analysis_runs WHERE label_id IN ({','.join('?' * len(label_ids))}) ORDER BY id DESC",
            list(label_ids),
        ).fetchall()
    return [RunRecord(**dict(row)) for row in rows]


def review_status_counts(db_path: Path | str | None = None) -> dict[str, int]:
    """How many discrepancies (latest analysis of each pair of files) are in each review status."""
    counts = {status: 0 for status in REVIEW_STATUSES}
    for item in list_review_queue(db_path=db_path):
        counts[item.status] = counts.get(item.status, 0) + 1
    return counts


def load_job_findings(job_id: int, db_path: Path | str | None = None) -> tuple[Finding, ...]:
    """The findings stored for a job, most severe first."""
    with closing(connect(db_path)) as connection:
        rows = connection.execute("SELECT * FROM analysis_findings WHERE job_id = ?", (job_id,)).fetchall()
    order = {"critical_review": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}
    findings = [_finding_from_row(row) for row in rows]
    findings.sort(key=lambda f: (f.result != RESULT_FLAGGED, order.get(f.severity, 9), f.rule_id, f.id))
    return tuple(findings)


def load_job_fields(job_id: int, db_path: Path | str | None = None) -> dict[str, dict[str, list[str]]]:
    """Extracted values stored for a job: {role: {field_key: [values]}}."""
    out: dict[str, dict[str, list[str]]] = {"approved": {}, "revised": {}}
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT role, field_key, value FROM extracted_fields WHERE job_id = ? ORDER BY role, field_key, position",
            (job_id,),
        ).fetchall()
    for row in rows:
        out[row["role"]].setdefault(row["field_key"], []).append(row["value"])
    return out


def link_run_to_label(job_id: int, label_id: str | None, db_path: Path | str | None = None) -> None:
    """Attach an analysis job to a label record in the catalog (or detach it with None)."""
    with closing(connect(db_path)) as connection, connection:
        row = connection.execute("SELECT job_uid, is_demo FROM analysis_runs WHERE id = ?", (job_id,)).fetchone()
        if row is None:
            raise ValueError(f"No analysis job with id {job_id}.")
        connection.execute("UPDATE analysis_runs SET label_id = ? WHERE id = ?", (label_id, job_id))
        audit.record(connection, audit.LABEL_LINKED, "analysis_job", row["job_uid"],
                     summary=f"Analysis linked to label record {label_id or '(none)'}",
                     details={"label_id": label_id}, job_uid=row["job_uid"], is_demo=bool(row["is_demo"]))


# --- Reviews of findings --------------------------------------------------------------------------
def check_reviewer(reviewer: str) -> str:
    """The cleaned reviewer name; raises ValueError if it is empty, too long or an automated name."""
    reviewer = " ".join((reviewer or "").split())
    if not reviewer:
        raise ValueError("A reviewer name or initials is required to change a review status.")
    if len(reviewer) > 60:
        raise ValueError("The reviewer name is too long (60 characters at most).")
    if reviewer.casefold() in RESERVED_REVIEWERS:
        raise ValueError(f"'{reviewer}' cannot be recorded as a reviewer: review decisions are made by people.")
    return reviewer


def set_review(
    approved_sha256: str,
    revised_sha256: str,
    finding_id: str,
    rule_id: str,
    severity: str,
    status: str,
    rationale: str = "",
    reviewer: str = "",
    db_path: Path | str | None = None,
) -> ReviewRecord:
    """Record a reviewer's decision, add it to the finding's history and to the audit log.

    Raises ValueError for an unknown status, a missing or automated reviewer name, a missing
    rationale when resolving or escalating, or a rationale that is too long.
    """
    if status not in REVIEW_STATUSES:
        raise ValueError(f"Unknown review status '{status}'. Use one of: {', '.join(REVIEW_STATUSES)}.")
    reviewer = check_reviewer(reviewer)
    rationale = " ".join((rationale or "").split())
    if status in RATIONALE_REQUIRED and not rationale:
        raise ValueError("A short rationale is required to resolve or escalate a finding.")
    if len(rationale) > MAX_RATIONALE:
        raise ValueError(f"The rationale is too long ({MAX_RATIONALE} characters at most).")
    now = _now()
    with closing(connect(db_path)) as connection, connection:
        row = connection.execute(
            "SELECT status FROM finding_reviews WHERE approved_sha256 = ? AND revised_sha256 = ? AND finding_id = ?",
            (approved_sha256, revised_sha256, finding_id),
        ).fetchone()
        previous = row["status"] if row else "pending"
        connection.execute(
            "INSERT INTO finding_reviews (approved_sha256, revised_sha256, finding_id, rule_id, severity, status, "
            "note, updated_at, reviewer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?) "
            "ON CONFLICT (approved_sha256, revised_sha256, finding_id) DO UPDATE SET "
            "status = excluded.status, note = excluded.note, updated_at = excluded.updated_at, "
            "reviewer = excluded.reviewer, rule_id = excluded.rule_id, severity = excluded.severity",
            (approved_sha256, revised_sha256, finding_id, rule_id, severity, status, rationale, now, reviewer),
        )
        connection.execute(
            "INSERT INTO review_events (approved_sha256, revised_sha256, finding_id, rule_id, from_status, "
            "to_status, note, at, reviewer) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (approved_sha256, revised_sha256, finding_id, rule_id, previous, status, rationale, now, reviewer),
        )
        job = connection.execute(
            "SELECT job_uid, is_demo FROM analysis_runs WHERE approved_sha256 = ? AND revised_sha256 = ? "
            "ORDER BY id DESC LIMIT 1", (approved_sha256, revised_sha256),
        ).fetchone()
        audit.record(
            connection, audit.REVIEW_STATUS_CHANGED, "finding", finding_id, actor=reviewer,
            summary=f"{rule_id}: {REVIEW_STATUS_LABELS.get(previous, previous)} → {REVIEW_STATUS_LABELS[status]}",
            details={"rule_id": rule_id, "severity": severity, "from_status": previous, "to_status": status,
                     "rationale": rationale, "approved_sha256": approved_sha256, "revised_sha256": revised_sha256},
            job_uid=job["job_uid"] if job else None, is_demo=bool(job["is_demo"]) if job else False,
        )
    return ReviewRecord(finding_id, rule_id, severity, status, rationale, now, reviewer)


def get_reviews(
    approved_sha256: str, revised_sha256: str, db_path: Path | str | None = None
) -> dict[str, ReviewRecord]:
    """Current decisions for one pair of files, by finding id. Findings never reviewed are absent (they are pending)."""
    with closing(connect(db_path)) as connection:
        rows = connection.execute(
            "SELECT finding_id, rule_id, severity, status, note AS rationale, updated_at, reviewer "
            "FROM finding_reviews WHERE approved_sha256 = ? AND revised_sha256 = ?",
            (approved_sha256, revised_sha256),
        ).fetchall()
    return {row["finding_id"]: ReviewRecord(**dict(row)) for row in rows}


def review_history(
    approved_sha256: str, revised_sha256: str, finding_id: str | None = None, db_path: Path | str | None = None
) -> list[ReviewEvent]:
    """The decisions made on a pair of files, oldest first (optionally for one finding)."""
    query = (
        "SELECT finding_id, rule_id, from_status, to_status, note AS rationale, at, reviewer FROM review_events "
        "WHERE approved_sha256 = ? AND revised_sha256 = ?"
    )
    params: list[str] = [approved_sha256, revised_sha256]
    if finding_id is not None:
        query += " AND finding_id = ?"
        params.append(finding_id)
    with closing(connect(db_path)) as connection:
        rows = connection.execute(query + " ORDER BY id", params).fetchall()
    return [ReviewEvent(**dict(row)) for row in rows]


def list_review_queue(
    *,
    statuses: tuple[str, ...] | None = None,
    include_not_verified: bool = False,
    db_path: Path | str | None = None,
) -> list[QueueItem]:
    """Findings from the latest analysis of each pair of files, with their current review state.

    By default only discrepancies are listed (not 'not verified' items), whatever their status;
    filter by `statuses` to build a to-do list. A resolved finding stays in the list, so nothing
    disappears from view.
    """
    sql = (
        "SELECT f.*, j.id AS job_id, j.job_uid, j.approved_filename, j.revised_filename, j.approved_sha256, "
        "j.revised_sha256, j.created_at, j.is_demo, COALESCE(r.status, 'pending') AS review_status, "
        "COALESCE(r.note, '') AS rationale, COALESCE(r.reviewer, '') AS reviewer, r.updated_at AS reviewed_at "
        "FROM analysis_findings f JOIN analysis_runs j ON j.id = f.job_id "
        "LEFT JOIN finding_reviews r ON r.approved_sha256 = j.approved_sha256 "
        "AND r.revised_sha256 = j.revised_sha256 AND r.finding_id = f.finding_id "
        "WHERE j.id = (SELECT MAX(id) FROM analysis_runs j2 WHERE j2.approved_sha256 = j.approved_sha256 "
        "AND j2.revised_sha256 = j.revised_sha256)"
    )
    if not include_not_verified:
        sql += " AND f.result = 'flagged'"
    with closing(connect(db_path)) as connection:
        rows = connection.execute(sql).fetchall()
    items = [
        QueueItem(
            finding=_finding_from_row(row), job_id=row["job_id"], job_uid=row["job_uid"],
            approved_filename=row["approved_filename"], revised_filename=row["revised_filename"],
            approved_sha256=row["approved_sha256"], revised_sha256=row["revised_sha256"],
            created_at=row["created_at"], is_demo=bool(row["is_demo"]), status=row["review_status"],
            rationale=row["rationale"], reviewer=row["reviewer"], updated_at=row["reviewed_at"],
        )
        for row in rows
    ]
    if statuses is not None:
        items = [item for item in items if item.status in statuses]
    rank = {"critical_review": 0, "high": 1, "medium": 2, "low": 3, "informational": 4}
    items.sort(key=lambda i: (rank.get(i.finding.severity, 9), -i.job_id, i.finding.rule_id, i.finding.id))
    return items
