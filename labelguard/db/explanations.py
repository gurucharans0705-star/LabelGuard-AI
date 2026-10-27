"""Storing explanations (AI-assisted or deterministic) so they can be read again later.

Only the explanation text, the finding ids and evidence references are stored: not the prompt, not
the API key, and not the label document.
"""
from __future__ import annotations

import json
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from labelguard.ai.models import AI_ASSISTED, Explanation, ExplanationItem
from labelguard.db import audit
from labelguard.db.audit import now as _now
from labelguard.db.database import connect


@dataclass(frozen=True)
class StoredExplanation:
    explanation_uid: str
    created_at: str
    job_id: int
    explanation: Explanation


def save_explanation(
    job_id: int, explanation: Explanation, *, actor: str = audit.SYSTEM_ACTOR, db_path: Path | str | None = None
) -> str:
    """Store an explanation for a job and add an audit event. Returns its id."""
    uid = f"EXP-{uuid.uuid4().hex[:12]}"
    content = json.dumps({
        "summary": explanation.summary,
        "items": [{"finding_id": i.finding_id, "text": i.text, "evidence_refs": list(i.evidence_refs),
                   "generated_by": i.generated_by} for i in explanation.items],
        "questions": list(explanation.questions),
        "finding_ids": list(explanation.finding_ids),
    })
    with closing(connect(db_path)) as c, c:
        job = c.execute("SELECT job_uid, is_demo FROM analysis_runs WHERE id = ?", (job_id,)).fetchone()
        if job is None:
            raise ValueError(f"No analysis job with id {job_id}.")
        c.execute(
            "INSERT INTO ai_explanations (explanation_uid, created_at, job_id, source, provider, model, note, "
            "content_json, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (uid, _now(), job_id, explanation.source, explanation.provider, explanation.model, explanation.note,
             content, job["is_demo"]),
        )
        kind = "AI-assisted" if explanation.source == AI_ASSISTED else "Deterministic"
        audit.record(
            c, audit.AI_EXPLANATION_CREATED, "explanation", uid, actor=actor,
            summary=f"{kind} explanation created for {len(explanation.finding_ids)} finding(s)",
            details={"source": explanation.source, "provider": explanation.provider, "model": explanation.model,
                     "findings": len(explanation.finding_ids), "fell_back": bool(explanation.note)},
            job_uid=job["job_uid"], is_demo=bool(job["is_demo"]),
        )
    return uid


def list_explanations(job_id: int, db_path: Path | str | None = None) -> list[StoredExplanation]:
    """Newest first."""
    with closing(connect(db_path)) as c:
        rows = c.execute("SELECT * FROM ai_explanations WHERE job_id = ? ORDER BY created_at DESC, rowid DESC",
                         (job_id,)).fetchall()
    out = []
    for row in rows:
        data = json.loads(row["content_json"])
        out.append(StoredExplanation(row["explanation_uid"], row["created_at"], row["job_id"], Explanation(
            source=row["source"], summary=data["summary"],
            items=tuple(ExplanationItem(i["finding_id"], i["text"], tuple(i["evidence_refs"]), i["generated_by"])
                        for i in data["items"]),
            questions=tuple(data["questions"]), model=row["model"], provider=row["provider"], note=row["note"],
            finding_ids=tuple(data["finding_ids"]),
        )))
    return out
