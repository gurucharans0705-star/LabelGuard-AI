"""Change Impact page: from a changed parameter to the label records that may need review.

The analysis itself lives in labelguard.impact and labelguard.workflow; this page only collects the
change, calls them and shows the result.
"""
from __future__ import annotations

import sqlite3

import pandas as pd
import streamlit as st

from labelguard import workflow
from labelguard.db import catalog as catalog_db
from labelguard.fields.models import FIELD_LABELS
from labelguard.impact.changes import changes_from_findings, suggest_config
from labelguard.impact.engine import ImpactError
from labelguard.impact.models import Catalog, ImpactResult, ParameterChange
from labelguard.impact.rules import ImpactRuleError
from labelguard.reporting.export import impact_to_markdown
from labelguard.ui.report_widget import render_report_download
from labelguard.rules.models import SEVERITY_LABELS, SEVERITY_NOTE
from labelguard.ui import state
from labelguard.ui.style import page_header

SYNTHETIC_NOTICE = (
    "This page uses a small **synthetic** catalog of products, label records, markets and languages stored in the "
    "local database. It is not connected to any PLM, ERP, SAP, FDA or EU regulatory system; see "
    "`docs/INTEGRATION_POINTS.md` for where such sources would plug in."
)


def render_impact_page() -> None:
    page_header("Change Impact", "Which label records may need review when a product parameter changes")
    st.caption(SYNTHETIC_NOTICE)
    catalog = state.load_catalog()
    if st.session_state.get("catalog_unreadable"):
        st.warning("The product catalog could not be read (the database may be read-only, locked or damaged), "
                   "so it is shown as empty. Your analysis results are not affected.")
    _render_demo_controls(catalog)
    if catalog.is_empty:
        return

    tab_analysis, tab_whatif, tab_saved = st.tabs(["From the current analysis", "What-if change", "Saved impact runs"])
    with tab_analysis:
        _render_from_analysis(catalog)
    with tab_whatif:
        _render_what_if(catalog)
    with tab_saved:
        _render_saved_runs(catalog)

    shown = st.session_state.get("impact_results")
    if shown:
        st.divider()
        _render_result(shown["result"], shown["run_uid"], catalog)


# --- Demo data -----------------------------------------------------------------------------------------------
def _render_demo_controls(catalog: Catalog) -> None:
    if catalog.is_empty:
        st.info("The product catalog is empty. Load the synthetic demonstration catalog to try change-impact analysis.")
        if st.button("Load synthetic demo catalog", type="primary", key="load_demo_catalog"):
            try:
                with st.spinner("Loading the synthetic catalog..."):
                    count = catalog_db.load_demo_data()
            except (sqlite3.Error, OSError):
                st.error("The demo catalog could not be saved (the database may be read-only or locked).")
                return
            st.success(f"Loaded {count} synthetic products.")
            st.rerun()
        return
    with st.expander("Demonstration data"):
        st.write(
            f"The catalog holds {len(catalog.products)} synthetic products and {len(catalog.labels)} label records. "
            "Clearing removes the synthetic catalog, its impact results and the analyses of the fictional sample "
            "labels (with their review decisions). **The audit history is kept**, including a record that the data "
            "was cleared."
        )
        confirm = st.checkbox("I understand what will be deleted", key="confirm_clear_demo")
        if st.button("Clear demonstration records", disabled=not confirm, key="clear_demo"):
            try:
                summary = catalog_db.clear_demo_data()
            except (sqlite3.Error, OSError):
                st.error("The demonstration records could not be cleared (the database may be read-only or locked).")
                return
            state.reset_after_demo_clear()
            st.success(
                f"Cleared {summary.products} products, {summary.labels} label records, {summary.analyses} analyses "
                f"and {summary.impact_runs} impact runs."
            )
            st.rerun()


# --- Choosing the change ------------------------------------------------------------------------------------
def _config_options(catalog: Catalog) -> dict[str, str]:
    return {
        f"{(catalog.product(c.product_id).name if catalog.product(c.product_id) else c.product_id)} · {c.name} ({c.config_id})": c.config_id
        for c in catalog.configurations
    }


def _render_from_analysis(catalog: Catalog) -> None:
    result = state.get_verification()
    if result is None:
        st.info("No analysis yet. Compare an approved and a revised label first; its changed fields can then be analysed here.")
        st.button("Go to Upload & Analyze", on_click=state.goto, args=("Upload & Analyze",), key="impact_go_upload")
        return
    detected = changes_from_findings(result.report.findings)
    if not detected:
        st.info("None of the flagged findings is a changed catalog parameter (product name, identifier, UDI, warnings, "
                "storage, sterilization, manufacturer, lot or dates), so there is nothing to analyse.")
        return
    options = _config_options(catalog)
    suggested = suggest_config(catalog, detected, st.session_state.get("label_id"))
    ids = list(options.values())
    index = ids.index(suggested) if suggested in ids else 0
    st.markdown("**Fields that changed between the two labels**")
    st.dataframe(
        pd.DataFrame([{"Parameter": FIELD_LABELS[d.parameter], "Approved label": d.old_value or "—",
                       "Revised label": d.new_value or "—", "Findings": ", ".join(d.finding_ids)} for d in detected]),
        hide_index=True, width="stretch",
    )
    st.caption(
        "The product configuration is suggested from values recorded in the catalog; confirm or change it."
        if suggested else "No configuration in the catalog records these old values. Choose the configuration yourself."
    )
    config_label = st.selectbox("Product configuration the change applies to", list(options), index=index, key="impact_config_a")
    change_label = st.selectbox(
        "Change to analyse", [d.parameter for d in detected], format_func=lambda key: FIELD_LABELS[key], key="impact_change_a")
    chosen = next(d for d in detected if d.parameter == change_label)
    if st.button("Analyze impact of this change", type="primary", key="run_impact_analysis"):
        _run(catalog, ParameterChange(options[config_label], chosen.parameter, chosen.old_value, chosen.new_value),
             source="analysis")


def _render_what_if(catalog: Catalog) -> None:
    st.write("Try a change that has not been made: nothing in the catalog is modified.")
    options = _config_options(catalog)
    config_label = st.selectbox("Product configuration", list(options), key="impact_config_w")
    config = catalog.configuration(options[config_label])
    parameter = st.selectbox("Parameter", list(FIELD_LABELS), format_func=FIELD_LABELS.get, key="impact_param_w")
    current = (config.parameters.get(parameter) if config else None) or ""
    st.text_input("Current value in the catalog", value=current or "(not recorded)", disabled=True, key=f"impact_cur_{options[config_label]}_{parameter}")
    new_value = st.text_input("New value", key="impact_new_w")
    if st.button("Analyze impact", type="primary", key="run_impact_whatif"):
        _run(catalog, ParameterChange(options[config_label], parameter, current or None, new_value.strip() or None),
             source="manual")


def _run(catalog: Catalog, change: ParameterChange, *, source: str) -> None:
    try:
        with st.spinner("Analyzing the impact of the change..."):
            result, run_uid = workflow.run_impact_analysis(
                catalog, change, source=source, job_uid=st.session_state.get("job_uid") if source == "analysis" else None,
                actor=state.get_reviewer() or "system",
            )
    except ImpactError as exc:
        st.error(str(exc))
        return
    except ImpactRuleError as exc:
        st.error(str(exc))
        st.caption("Fix `config/impact_rules.toml` and run the analysis again.")
        return
    except (sqlite3.Error, OSError):
        st.error("The impact result could not be saved (the database may be read-only or locked).")
        return
    st.session_state["impact_results"] = {"result": result, "run_uid": run_uid}


# --- Showing a result ---------------------------------------------------------------------------------------
def _render_result(result: ImpactResult, run_uid: str, catalog: Catalog) -> None:
    change = result.change
    config = catalog.configuration(change.config_id)
    st.subheader("Impact of the change")
    st.markdown(
        f"**Changed parameter:** {FIELD_LABELS[change.parameter]} · **Configuration:** "
        f"{config.name if config else change.config_id} · run `{run_uid}`"
    )
    left, right = st.columns(2, gap="large")
    left.markdown("**Old value**"); left.code(change.old_value or "not recorded", language=None)
    right.markdown("**New value**"); right.code(change.new_value or "not recorded", language=None)

    markets = sorted({i.market_name for i in result.items})
    languages = sorted({i.language_name for i in result.items})
    a, b, c, d = st.columns(4)
    a.metric("Labels that may be affected", result.label_count)
    b.metric("Product records", len(result.products))
    c.metric("Markets", len(markets))
    d.metric("Languages", len(languages))
    for note in result.notes:
        st.info(note)

    st.markdown("**Affected product records**")
    st.dataframe(pd.DataFrame([{"Product": p.name, "Configuration": p.config_name or "—", "Why": p.why}
                               for p in result.products]), hide_index=True, width="stretch")
    if not result.items:
        st.success("No label record was flagged by the configured impact rules for this change.")
        st.caption("That is not a statement that no label is affected: only relationships recorded in the catalog "
                   "and rules in `impact_rules.toml` are considered.")
    else:
        st.markdown("**Potentially affected labels**")
        st.caption(f"Markets: {', '.join(markets)} · Languages: {', '.join(languages)}. " + SEVERITY_NOTE)
        st.dataframe(pd.DataFrame([_row(i, catalog) for i in result.items]), hide_index=True, width="stretch")
        for item in result.items:
            with st.expander(f"{SEVERITY_LABELS.get(item.severity, item.severity)} · {item.label.label_code} v{item.label.version} · "
                             f"{item.market_name} / {item.language_name} / {item.packaging_name} · {item.rule_id}"):
                st.markdown("**Why this label may be affected**")
                st.write(item.relationship)
                st.write(item.reason)
                st.markdown("**Recommended review actions**")
                for action in item.actions:
                    st.write(f"- {action}")
                records = catalog.records_of_label(item.label.label_id)
                if records:
                    st.markdown("**Associated review records (synthetic)**")
                    st.dataframe(pd.DataFrame([{"Reviewed": r.reviewed_at[:10], "Reviewer": r.reviewer, "Outcome": r.outcome,
                                                "Summary": r.summary} for r in records]), hide_index=True, width="stretch")
    if result.excluded:
        with st.expander(f"Labels of this product that were not flagged ({len(result.excluded)})"):
            st.dataframe(pd.DataFrame([{"Label": f"{x.label.label_code} v{x.label.version}", "Record": x.label.label_id,
                                        "Market": catalog.market_name(x.label.market_code), "Reason": x.reason}
                                       for x in result.excluded]), hide_index=True, width="stretch")
    render_report_download(
        "Download impact report (Markdown)", lambda: impact_to_markdown(result, catalog), kind="Impact report",
        filename="labelguard_impact_report.md", mime="text/markdown", key="download_impact_report",
    )


def _row(item, catalog: Catalog) -> dict[str, str]:
    records = catalog.records_of_label(item.label.label_id)
    last = f"{records[0].outcome} ({records[0].reviewed_at[:10]})" if records else "none"
    return {
        "Priority": SEVERITY_LABELS.get(item.severity, item.severity),
        "Label": f"{item.label.label_code} v{item.label.version}",
        "Status": item.label.status.replace("_", " "),
        "Product": item.product_name, "Configuration": item.config_name,
        "Market": item.market_name, "Language": item.language_name, "Packaging": item.packaging_name,
        "Rule": item.rule_id, "Reason": item.reason, "Recommended actions": item.action_text, "Last review record": last,
    }


def _render_saved_runs(catalog: Catalog) -> None:
    try:
        runs = catalog_db.list_impact_runs(20)
    except (sqlite3.Error, OSError):
        st.caption("Saved impact runs could not be read.")
        return
    if not runs:
        st.info("No impact analysis has been run yet.")
        return
    st.dataframe(pd.DataFrame([{
        "Run": r.run_uid, "When (UTC)": r.created_at.replace("T", " ").replace("+00:00", ""),
        "Product": (catalog.product(r.product_id).name if catalog.product(r.product_id) else r.product_id),
        "Parameter": FIELD_LABELS.get(r.parameter_key, r.parameter_key), "Old": r.old_value or "—", "New": r.new_value or "—",
        "Labels": r.item_count, "Source": "current analysis" if r.source == "analysis" else "what-if",
    } for r in runs]), hide_index=True, width="stretch")
