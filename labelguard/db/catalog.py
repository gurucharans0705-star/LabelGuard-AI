"""Storing and reading the product catalog and the change-impact results.

The catalog is synthetic demonstration data (every row is flagged is_demo), kept local. This module
is where a real source (PLM, ERP) would be connected: anything that can build a `Catalog` can be
analysed; see docs/INTEGRATION_POINTS.md.
"""
from __future__ import annotations

import sqlite3
import uuid
from contextlib import closing
from dataclasses import dataclass
from pathlib import Path

from labelguard.db import audit
from labelguard.db.audit import now as _now
from labelguard.db.database import connect
from labelguard.impact.models import (
    Catalog,
    ImpactResult,
    LabelReviewRecord,
    LabelVersion,
    Language,
    Market,
    PackagingLevel,
    Product,
    ProductConfiguration,
    ProductRelationship,
)

_DEMO_TABLES_IN_DELETE_ORDER = (
    "impact_items", "impact_runs", "label_review_records", "label_required_fields", "label_versions",
    "product_relationships", "config_parameters", "product_configurations", "products", "markets", "languages",
    "packaging_levels",
)


def save_catalog(catalog: Catalog, *, is_demo: bool = True, db_path: Path | str | None = None) -> None:
    """Insert or replace the catalog's records (idempotent: loading twice gives the same rows)."""
    flag = int(is_demo)
    with closing(connect(db_path)) as c, c:
        for m in catalog.markets:
            c.execute("INSERT OR REPLACE INTO markets (code, name, is_demo) VALUES (?, ?, ?)", (m.code, m.name, flag))
        for lang in catalog.languages:
            c.execute("INSERT OR REPLACE INTO languages (code, name, is_demo) VALUES (?, ?, ?)", (lang.code, lang.name, flag))
        for p in catalog.packaging_levels:
            c.execute("INSERT OR REPLACE INTO packaging_levels (code, name, rank, is_demo) VALUES (?, ?, ?, ?)",
                      (p.code, p.name, p.rank, flag))
        for p in catalog.products:
            c.execute("INSERT OR REPLACE INTO products (product_id, name, category, source_language, is_demo) "
                      "VALUES (?, ?, ?, ?, ?)", (p.product_id, p.name, p.category, p.source_language, flag))
        for cfg in catalog.configurations:
            c.execute("INSERT OR REPLACE INTO product_configurations (config_id, product_id, name, is_demo) "
                      "VALUES (?, ?, ?, ?)", (cfg.config_id, cfg.product_id, cfg.name, flag))
            c.execute("DELETE FROM config_parameters WHERE config_id = ?", (cfg.config_id,))
            for key, value in cfg.parameters.items():
                c.execute("INSERT INTO config_parameters (config_id, parameter_key, value) VALUES (?, ?, ?)",
                          (cfg.config_id, key, value))
        for r in catalog.relationships:
            c.execute("INSERT OR REPLACE INTO product_relationships (product_id, related_product_id, kind, note) "
                      "VALUES (?, ?, ?, ?)", (r.product_id, r.related_product_id, r.kind, r.note))
        for label in catalog.labels:
            c.execute(
                "INSERT OR REPLACE INTO label_versions (label_id, label_code, version, status, product_id, config_id, "
                "market_code, language_code, packaging_level_code, document_sha256, is_demo) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (label.label_id, label.label_code, label.version, label.status, label.product_id, label.config_id,
                 label.market_code, label.language_code, label.packaging_level_code, label.document_sha256, flag))
            c.execute("DELETE FROM label_required_fields WHERE label_id = ?", (label.label_id,))
            for key in label.required_fields:
                c.execute("INSERT INTO label_required_fields (label_id, field_key) VALUES (?, ?)", (label.label_id, key))
        for rec in catalog.review_records:
            c.execute("INSERT OR REPLACE INTO label_review_records (record_id, label_id, reviewed_at, reviewer, outcome, "
                      "summary, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?)",
                      (rec.record_id, rec.label_id, rec.reviewed_at, rec.reviewer, rec.outcome, rec.summary, flag))


def load_catalog(db_path: Path | str | None = None) -> Catalog:
    """The whole catalog as an in-memory snapshot (empty if nothing has been loaded)."""
    with closing(connect(db_path)) as c:
        params: dict[str, dict[str, str]] = {}
        for row in c.execute("SELECT config_id, parameter_key, value FROM config_parameters"):
            params.setdefault(row["config_id"], {})[row["parameter_key"]] = row["value"]
        required: dict[str, list[str]] = {}
        for row in c.execute("SELECT label_id, field_key FROM label_required_fields ORDER BY field_key"):
            required.setdefault(row["label_id"], []).append(row["field_key"])
        return Catalog(
            products=tuple(Product(r["product_id"], r["name"], r["category"], r["source_language"])
                           for r in c.execute("SELECT * FROM products ORDER BY product_id")),
            configurations=tuple(
                ProductConfiguration(r["config_id"], r["product_id"], r["name"], params.get(r["config_id"], {}))
                for r in c.execute("SELECT * FROM product_configurations ORDER BY config_id")),
            labels=tuple(
                LabelVersion(r["label_id"], r["label_code"], r["version"], r["status"], r["product_id"], r["config_id"],
                             r["market_code"], r["language_code"], r["packaging_level_code"],
                             tuple(required.get(r["label_id"], [])), r["document_sha256"])
                for r in c.execute("SELECT * FROM label_versions ORDER BY label_id")),
            markets=tuple(Market(r["code"], r["name"]) for r in c.execute("SELECT * FROM markets ORDER BY code")),
            languages=tuple(Language(r["code"], r["name"]) for r in c.execute("SELECT * FROM languages ORDER BY code")),
            packaging_levels=tuple(PackagingLevel(r["code"], r["name"], r["rank"])
                                   for r in c.execute("SELECT * FROM packaging_levels ORDER BY rank")),
            relationships=tuple(ProductRelationship(r["product_id"], r["related_product_id"], r["kind"], r["note"])
                                for r in c.execute("SELECT * FROM product_relationships")),
            review_records=tuple(LabelReviewRecord(r["record_id"], r["label_id"], r["reviewed_at"], r["reviewer"],
                                                   r["outcome"], r["summary"])
                                 for r in c.execute("SELECT * FROM label_review_records")),
        )


def has_demo_data(db_path: Path | str | None = None) -> bool:
    with closing(connect(db_path)) as c:
        return c.execute("SELECT 1 FROM products WHERE is_demo = 1 LIMIT 1").fetchone() is not None


def load_demo_data(db_path: Path | str | None = None) -> int:
    """Load the synthetic catalog and record the event. Returns the number of products."""
    from labelguard.impact.demo_data import demo_catalog

    catalog = demo_catalog()
    save_catalog(catalog, is_demo=True, db_path=db_path)
    audit.log_event(audit.DEMO_DATA_LOADED, "demo_data", "catalog", summary=(
        f"Synthetic catalog loaded: {len(catalog.products)} products, {len(catalog.labels)} labels"),
        details={"products": len(catalog.products), "labels": len(catalog.labels)}, is_demo=True, db_path=db_path)
    return len(catalog.products)


@dataclass(frozen=True)
class ClearSummary:
    products: int
    labels: int
    impact_runs: int
    analyses: int
    findings: int


def clear_demo_data(db_path: Path | str | None = None) -> ClearSummary:
    """Delete the synthetic demonstration records (catalog, impact results, and analyses of sample files).

    The audit log is kept: it records that the data existed and that it was cleared. Review
    decisions on demo analyses are removed with them; their history stays in the audit log.
    """
    with closing(connect(db_path)) as c, c:
        count = lambda sql: c.execute(sql).fetchone()[0]  # noqa: E731
        summary_products = count("SELECT COUNT(*) FROM products WHERE is_demo = 1")
        summary_labels = count("SELECT COUNT(*) FROM label_versions WHERE is_demo = 1")
        summary_impact = count("SELECT COUNT(*) FROM impact_runs WHERE is_demo = 1")
        demo_jobs = [r["id"] for r in c.execute("SELECT id FROM analysis_runs WHERE is_demo = 1")]
        pairs = c.execute("SELECT DISTINCT approved_sha256, revised_sha256 FROM analysis_runs WHERE is_demo = 1").fetchall()
        findings = 0
        for job_id in demo_jobs:
            findings += c.execute("SELECT COUNT(*) FROM analysis_findings WHERE job_id = ?", (job_id,)).fetchone()[0]
            for table in ("analysis_findings", "extracted_fields", "ai_explanations"):
                c.execute(f"DELETE FROM {table} WHERE job_id = ?", (job_id,))
        for pair in pairs:
            for table in ("finding_reviews", "review_events"):
                c.execute(f"DELETE FROM {table} WHERE approved_sha256 = ? AND revised_sha256 = ?", tuple(pair))
        c.execute("DELETE FROM analysis_runs WHERE is_demo = 1")
        c.execute("DELETE FROM impact_items WHERE run_uid IN (SELECT run_uid FROM impact_runs WHERE is_demo = 1)")
        c.execute("DELETE FROM impact_runs WHERE is_demo = 1")
        for table in _DEMO_TABLES_IN_DELETE_ORDER:
            if table not in ("impact_items", "impact_runs"):
                _delete_demo(c, table)
        audit.record(c, audit.DEMO_DATA_CLEARED, "demo_data", "catalog", summary=(
            f"Synthetic demonstration records cleared: {summary_products} products, {summary_labels} labels, "
            f"{len(demo_jobs)} analyses, {summary_impact} impact runs"),
            details={"products": summary_products, "labels": summary_labels, "analyses": len(demo_jobs),
                     "impact_runs": summary_impact, "findings": findings}, is_demo=True)
    return ClearSummary(summary_products, summary_labels, summary_impact, len(demo_jobs), findings)


def _delete_demo(c: sqlite3.Connection, table: str) -> None:
    """Delete demo rows from a catalog table (child tables have no flag of their own)."""
    if table in ("label_required_fields",):
        c.execute("DELETE FROM label_required_fields WHERE label_id IN (SELECT label_id FROM label_versions WHERE is_demo = 1)")
    elif table == "config_parameters":
        c.execute("DELETE FROM config_parameters WHERE config_id IN (SELECT config_id FROM product_configurations WHERE is_demo = 1)")
    elif table == "product_relationships":
        c.execute("DELETE FROM product_relationships WHERE product_id IN (SELECT product_id FROM products WHERE is_demo = 1) "
                  "OR related_product_id IN (SELECT product_id FROM products WHERE is_demo = 1)")
    else:
        c.execute(f"DELETE FROM {table} WHERE is_demo = 1")


# --- Impact results -------------------------------------------------------------------------------
def save_impact_result(
    result: ImpactResult,
    *,
    source: str = "manual",
    job_uid: str | None = None,
    actor: str = audit.SYSTEM_ACTOR,
    is_demo: bool = True,
    db_path: Path | str | None = None,
) -> str:
    """Store an impact analysis with its items and add an audit event. Returns the run id."""
    change = result.change
    run_uid = f"IMP-{uuid.uuid4().hex[:12]}"
    with closing(connect(db_path)) as c, c:
        config = c.execute("SELECT product_id FROM product_configurations WHERE config_id = ?", (change.config_id,)).fetchone()
        product_id = config["product_id"] if config else ""
        c.execute(
            "INSERT INTO impact_runs (run_uid, created_at, product_id, config_id, parameter_key, old_value, new_value, "
            "source, job_uid, item_count, is_demo) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
            (run_uid, _now(), product_id, change.config_id, change.parameter, _short(change.old_value),
             _short(change.new_value), source, job_uid, result.label_count, int(is_demo)),
        )
        for item in result.items:
            c.execute(
                "INSERT OR REPLACE INTO impact_items (run_uid, label_id, label_code, label_version, product_id, config_id, "
                "market_code, language_code, packaging_code, rule_id, severity, relationship, reason, action) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (run_uid, item.label.label_id, item.label.label_code, item.label.version, item.label.product_id,
                 item.label.config_id, item.label.market_code, item.label.language_code, item.label.packaging_level_code,
                 item.rule_id, item.severity, item.relationship, item.reason, item.action_text),
            )
        audit.record(
            c, audit.IMPACT_ANALYSIS_RUN, "impact_run", run_uid, actor=actor,
            summary=f"Impact of {change.parameter} change on {change.config_id}: {result.label_count} label(s) may be affected",
            details={"parameter": change.parameter, "config_id": change.config_id, "labels": result.label_count,
                     "source": source}, job_uid=job_uid, is_demo=is_demo,
        )
    return run_uid


def _short(text: str | None, limit: int = 500) -> str | None:
    if text is None:
        return None
    text = " ".join(text.split())
    return text if len(text) <= limit else text[: limit - 1] + "…"


@dataclass(frozen=True)
class ImpactRunRecord:
    run_uid: str
    created_at: str
    product_id: str
    config_id: str
    parameter_key: str
    old_value: str | None
    new_value: str | None
    source: str
    job_uid: str | None
    item_count: int
    is_demo: bool


def list_impact_runs(
    limit: int = 50, db_path: Path | str | None = None, *, product_id: str | None = None
) -> list[ImpactRunRecord]:
    """Newest first; optionally only the runs about one product."""
    where, params = ("WHERE product_id = ?", [product_id]) if product_id else ("", [])
    with closing(connect(db_path)) as c:
        rows = c.execute(f"SELECT * FROM impact_runs {where} ORDER BY created_at DESC, rowid DESC LIMIT ?",
                         [*params, max(1, int(limit))]).fetchall()
    return [ImpactRunRecord(**{**dict(r), "is_demo": bool(r["is_demo"])}) for r in rows]


def load_impact_items(run_uid: str, db_path: Path | str | None = None) -> list[dict]:
    with closing(connect(db_path)) as c:
        rows = c.execute("SELECT * FROM impact_items WHERE run_uid = ? ORDER BY label_code, label_id, rule_id", (run_uid,)).fetchall()
    return [dict(r) for r in rows]
