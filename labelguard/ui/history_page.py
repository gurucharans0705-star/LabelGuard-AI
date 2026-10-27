"""Product History page: products and their label records, and every saved analysis job."""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from labelguard.db import audit, catalog as catalog_db, database
from labelguard.db.database import REVIEW_STATUS_LABELS
from labelguard.fields.models import FIELD_LABELS
from labelguard.impact.models import LABEL_STATUS_NAMES, Catalog
from labelguard.ui import state
from labelguard.ui.style import page_header


def render_history_page() -> None:
    page_header("Product History", "Products, their label records, and the analyses saved for them")
    tab_products, tab_jobs = st.tabs(["Products and labels", "Analysis jobs"])
    with tab_products:
        _render_products(state.load_catalog())
    with tab_jobs:
        _render_jobs()


def _when(text: str) -> str:
    return text.replace("T", " ").replace("+00:00", "")


# --- Products and labels ------------------------------------------------------------------------------------
def _render_products(catalog: Catalog) -> None:
    st.caption("Synthetic demonstration records; not read from any PLM or ERP system.")
    if catalog.is_empty:
        st.info("The product catalog is empty. Load the synthetic demonstration catalog on the Change Impact page.")
        st.button("Go to Change Impact", on_click=state.goto, args=("Change Impact",), key="history_go_impact")
        return
    product_id = st.selectbox("Product", [p.product_id for p in catalog.products], key="history_product",
                              format_func=lambda pid: f"{catalog.product(pid).name} ({pid})")
    product = catalog.product(product_id)
    configs, labels = catalog.configs_of_product(product_id), catalog.labels_of_product(product_id)
    st.markdown(f"**{product.name}** · {product.category or 'no category'} · source language {catalog.language_name(product.source_language)}")
    for relation in catalog.relationships:
        if relation.product_id == product_id:
            other = catalog.product(relation.related_product_id)
            st.caption(f"Contains: {other.name if other else relation.related_product_id}. {relation.note}")
        elif relation.related_product_id == product_id:
            other = catalog.product(relation.product_id)
            st.caption(f"Is a component of: {other.name if other else relation.product_id}.")

    st.markdown("**Configurations**")
    for config in configs:
        with st.expander(f"{config.name} ({config.config_id})"):
            st.dataframe(pd.DataFrame([{"Parameter": FIELD_LABELS.get(k, k), "Recorded value": v}
                                       for k, v in config.parameters.items()]), hide_index=True, width="stretch")

    try:
        runs = database.list_runs_for_labels([l.label_id for l in labels])
        impact_runs = catalog_db.list_impact_runs(20, product_id=product_id)
    except (sqlite3.Error, OSError):
        runs, impact_runs = [], []
        st.warning("Saved analyses could not be read.")
    linked: dict[str, int] = {}
    for run in runs:
        linked[run.label_id] = linked.get(run.label_id, 0) + 1

    st.markdown("**Label records**")
    st.dataframe(pd.DataFrame([{
        "Record": l.label_id, "Label": f"{l.label_code} v{l.version}", "Status": LABEL_STATUS_NAMES.get(l.status, l.status),
        "Configuration": (catalog.configuration(l.config_id).name if catalog.configuration(l.config_id) else l.config_id),
        "Market": catalog.market_name(l.market_code), "Language": catalog.language_name(l.language_code),
        "Packaging": catalog.packaging_name(l.packaging_level_code), "Fields shown": len(l.required_fields),
        "Review records": len(catalog.records_of_label(l.label_id)), "Linked analyses": linked.get(l.label_id, 0),
    } for l in labels]), hide_index=True, width="stretch")

    label_id = st.selectbox("Label record history", [l.label_id for l in labels], key="history_label",
                            format_func=lambda lid: f"{catalog.label(lid).label_code} v{catalog.label(lid).version} · {lid}")
    label = catalog.label(label_id)
    st.markdown(f"**Fields this label shows:** {', '.join(FIELD_LABELS.get(k, k) for k in label.required_fields) or 'none recorded'}")
    records = catalog.records_of_label(label_id)
    st.markdown("**Review records (synthetic)**")
    if records:
        st.dataframe(pd.DataFrame([{"Reviewed": _when(r.reviewed_at), "Reviewer": r.reviewer, "Outcome": r.outcome,
                                    "Summary": r.summary} for r in records]), hide_index=True, width="stretch")
    else:
        st.caption("No review record for this label.")
    st.markdown("**Analyses linked to this label**")
    mine = [r for r in runs if r.label_id == label_id]
    if mine:
        st.dataframe(pd.DataFrame([_run_row(r) for r in mine]), hide_index=True, width="stretch")
    else:
        st.caption("No analysis has been linked to this label record. Choose it under Upload & Analyze before analyzing.")
    st.markdown("**Impact analyses for this product**")
    if impact_runs:
        st.dataframe(pd.DataFrame([{"Run": r.run_uid, "When (UTC)": _when(r.created_at),
                                    "Parameter": FIELD_LABELS.get(r.parameter_key, r.parameter_key),
                                    "Labels": r.item_count} for r in impact_runs]), hide_index=True, width="stretch")
    else:
        st.caption("No impact analysis has been run for this product.")


# --- Analysis jobs -------------------------------------------------------------------------------------------
def _run_row(run: database.RunRecord) -> dict[str, object]:
    return {
        "Job": run.job_uid, "When (UTC)": _when(run.created_at), "Approved label": run.approved_filename,
        "Revised label": run.revised_filename, "Flagged": run.findings_flagged, "Critical review": run.findings_critical,
        "Not verified": run.findings_not_evaluated, "Linked label": run.label_id or "—",
        "Sample data": "yes" if run.is_demo else "no",
    }


def _render_jobs() -> None:
    try:
        runs = database.list_runs(100)
    except (sqlite3.Error, OSError):
        st.error("The analysis history could not be read (the database may be read-only or locked).")
        return
    if not runs:
        st.info("No analysis has been saved yet. Each analysis you run is stored here (file names, fingerprints, "
                "extracted field values, findings and decisions; never the PDFs or the full label text).")
        return
    st.dataframe(pd.DataFrame([_run_row(r) for r in runs]), hide_index=True, width="stretch")
    choice = st.selectbox("Open a saved analysis", [r.id for r in runs], key="history_job",
                          format_func=lambda i: next(f"{r.job_uid} · {r.approved_filename} → {r.revised_filename}" for r in runs if r.id == i))
    run = next(r for r in runs if r.id == choice)
    findings = database.load_job_findings(run.id)
    reviews = database.get_reviews(run.approved_sha256, run.revised_sha256)
    st.markdown(f"**Findings stored for {run.job_uid}** ({len(findings)})")
    if findings:
        st.dataframe(pd.DataFrame([{
            "Priority": f.severity_label, "Rule": f.rule_id, "Finding": f.title,
            "Result": "Discrepancy" if f.result == "flagged" else "Not verified",
            "Review": REVIEW_STATUS_LABELS[reviews[f.id].status] if f.id in reviews else REVIEW_STATUS_LABELS["pending"],
            "Reviewer": reviews[f.id].reviewer if f.id in reviews else "—",
        } for f in findings]), hide_index=True, width="stretch")
    else:
        st.caption("This analysis stored no findings.")
    fields = database.load_job_fields(run.id)
    with st.expander("Extracted field values stored for this analysis"):
        rows = [{"Field": FIELD_LABELS.get(k, k), "Approved label": "; ".join(fields["approved"].get(k, [])) or "not detected",
                 "Revised label": "; ".join(fields["revised"].get(k, [])) or "not detected"} for k in FIELD_LABELS]
        st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")
    with st.expander("Audit events for this analysis"):
        events = audit.list_events(job_uid=run.job_uid, limit=200)
        st.dataframe(pd.DataFrame([{"When (UTC)": _when(e.at), "Event": e.type_label, "By": e.actor, "Summary": e.summary}
                                   for e in events]), hide_index=True, width="stretch")
