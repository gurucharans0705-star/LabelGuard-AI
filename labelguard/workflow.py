"""Operations that combine the engines with storage. The pages call these; they hold no logic of their own.

Keeping them here means the Streamlit pages stay thin and the same behaviour is tested once.
"""
from __future__ import annotations

import hashlib
from pathlib import Path

from labelguard.ai import service as ai_service
from labelguard.ai.models import Explanation
from labelguard.ai.provider import Provider
from labelguard.db import audit, catalog, database, explanations
from labelguard.impact.engine import analyze_impact
from labelguard.impact.models import Catalog, ImpactResult, ParameterChange
from labelguard.impact.rules import load_impact_rules
from labelguard.rules.models import Finding, Rule


def record_report_generated(
    kind: str, filename: str, content: str | bytes, *, job_uid: str | None = None,
    actor: str = audit.SYSTEM_ACTOR, is_demo: bool = False, db_path: Path | str | None = None,
) -> str:
    """Log that a report was produced for download. The content's fingerprint is stored, never the content."""
    data = content.encode("utf-8") if isinstance(content, str) else content
    return audit.log_event(
        audit.REPORT_GENERATED, "report", filename, actor=actor, summary=f"{kind} generated ({filename})",
        details={"kind": kind, "filename": filename, "sha256": hashlib.sha256(data).hexdigest(), "bytes": len(data)},
        job_uid=job_uid, is_demo=is_demo, db_path=db_path,
    )


def run_impact_analysis(
    catalog_snapshot: Catalog, change: ParameterChange, *, source: str = "manual", job_uid: str | None = None,
    actor: str = audit.SYSTEM_ACTOR, db_path: Path | str | None = None,
) -> tuple[ImpactResult, str]:
    """Analyze a parameter change against the catalog, store the result and log it. Returns (result, run id)."""
    result = analyze_impact(catalog_snapshot, change, load_impact_rules())
    run_uid = catalog.save_impact_result(result, source=source, job_uid=job_uid, actor=actor, db_path=db_path)
    return result, run_uid


def generate_explanation(
    job_id: int, findings: tuple[Finding, ...], rules: list[Rule], *, mode: str = ai_service.MODE_DETERMINISTIC,
    provider: Provider | None = None, actor: str = audit.SYSTEM_ACTOR, db_path: Path | str | None = None,
) -> tuple[Explanation, str]:
    """Explain a job's findings (AI-assisted if requested and possible, else deterministic) and store it."""
    explanation = ai_service.explain(findings, rules, mode=mode, provider=provider)
    return explanation, explanations.save_explanation(job_id, explanation, actor=actor, db_path=db_path)
