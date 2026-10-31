"""AI Explanations page: a plain-language restatement of the findings, never a decision.

Two modes: deterministic (local, nothing leaves the computer) and AI-assisted (Claude API, only when a key is
configured and the person asks for it). Either way the explanation restates findings the rules already produced and
cites their evidence; it cannot change a finding or a review status.
"""
from __future__ import annotations

import sqlite3

import streamlit as st

from labelguard import workflow
from labelguard.ai import config as ai_config
from labelguard.ai import service as ai_service
from labelguard.ai.models import AI_ASSISTED, Explanation
from labelguard.ai.prompt import resolve_ref
from labelguard.db import database, explanations as explanation_db
from labelguard.reporting.export import explanation_to_markdown
from labelguard.ui.report_widget import render_report_download
from labelguard.rules.models import Finding
from labelguard.safe_text import escape_markdown as md
from labelguard.ui import state
from labelguard.ui.style import badge, page_header

AI_NOTICE = (
    "Explanations only restate findings that the deterministic rules already produced. They cannot change a finding, "
    "approve or release a label, or replace a qualified reviewer, and they are not a compliance determination."
)


def render_ai_page() -> None:
    page_header("AI Explanations", "Plain-language explanations of findings, linked to their evidence")
    result = state.get_verification()
    if result is None:
        st.info("No analysis yet. Compare two labels first; its findings can then be explained here.")
        st.button("Go to Upload & Analyze", type="primary", on_click=state.goto, args=("Upload & Analyze",), key="ai_go_upload")
        return
    st.caption(AI_NOTICE)
    findings = result.report.findings
    if not findings:
        st.success("There are no findings to explain.")
        return

    settings = ai_config.get_settings()
    mode = _render_mode_choice(settings)
    st.write(f"{len(findings)} finding(s) from the current analysis can be explained"
             + (f" (the {ai_service.prompt.MAX_FINDINGS} most severe are used)." if len(findings) > ai_service.prompt.MAX_FINDINGS else "."))
    label = "Generate AI-assisted explanation" if mode == ai_service.MODE_AI else "Generate explanation"
    allowed = mode != ai_service.MODE_AI or bool(st.session_state.get("ai_confirm"))
    if st.button(label, type="primary", disabled=not allowed, key="generate_explanation"):
        _generate(result, findings, mode)
    error = st.session_state.get("explanation_error")
    if error:
        st.warning(error)
    shown = st.session_state.get("explanation")
    if shown is not None:
        st.divider()
        _render_explanation(shown, findings)
    _render_saved(findings)


def _render_mode_choice(settings: ai_config.AiSettings) -> str:
    st.markdown("**How should the explanation be produced?**")
    options = {"Deterministic (local; nothing is sent anywhere)": ai_service.MODE_DETERMINISTIC}
    if settings.configured:
        options["AI-assisted (sends the findings' text and evidence quotes to the Claude API)"] = ai_service.MODE_AI
    choice = st.radio("Mode", list(options), key="ai_mode", label_visibility="collapsed")
    mode = options[choice]
    if not settings.configured:
        st.info(settings.status_message + " To use AI assistance, set the environment variable "
                f"`{ai_config.KEY_VARIABLE}` before starting the app (see the README). Everything else works without it.")
    elif mode == ai_service.MODE_AI:
        st.warning(
            f"AI-assisted mode sends finding titles, values and evidence quotes from these labels to the Claude API "
            f"(model `{settings.model}`). No PDF and no API key is included in the text. Only continue if that is allowed "
            "for these documents.")
        st.checkbox("I confirm that these label excerpts may be sent to the Claude API", key="ai_confirm")
    else:
        st.caption(settings.status_message)
    return mode


def _generate(result, findings: tuple[Finding, ...], mode: str) -> None:
    st.session_state.pop("explanation_error", None)
    job_id = st.session_state.get("run_id")
    with st.spinner("Preparing the explanation..."):
        try:
            if job_id:
                explanation, _uid = workflow.generate_explanation(job_id, findings, list(result.rules), mode=mode,
                                                                  actor=state.get_reviewer() or "system")
            else:  # the database was unusable when the analysis ran: explain without saving
                explanation = ai_service.explain(findings, list(result.rules), mode=mode)
                st.session_state["explanation_error"] = "The explanation could not be saved because the database is unavailable."
        except (sqlite3.Error, OSError):
            explanation = ai_service.explain(findings, list(result.rules), mode=mode)
            st.session_state["explanation_error"] = "The explanation was produced but could not be saved (database problem)."
    st.session_state["explanation"] = explanation


def _render_explanation(explanation: Explanation, findings: tuple[Finding, ...]) -> None:
    kind = "ok" if explanation.source == AI_ASSISTED else "info"
    st.markdown(f"{badge(explanation.label, kind)} &nbsp; " + (f"model `{explanation.model}`" if explanation.model else "no model used"),
                unsafe_allow_html=True)
    if explanation.source == AI_ASSISTED:
        st.caption("AI-assisted text was checked against the evidence before it was shown. Verify it against the labels.")
    if explanation.note:
        st.warning(md(explanation.note))
    st.markdown("**Summary for the reviewer**")
    st.markdown(md(explanation.summary))
    by_id = {f.id: f for f in findings}
    st.markdown("**Explanation of each finding**")
    for item in explanation.items:
        finding = by_id.get(item.finding_id)
        title = f"{finding.severity_label} · {finding.rule_id} · {finding.title}" if finding else item.finding_id
        with st.expander(title):
            st.markdown(badge("AI-assisted" if item.generated_by == AI_ASSISTED else "Deterministic",
                              "ok" if item.generated_by == AI_ASSISTED else "info"), unsafe_allow_html=True)
            st.markdown(md(item.text))
            if finding:
                st.caption(f"Finding ID {finding.id} · original values: approved \"{md(finding.approved_value) or '—'}\", "
                           f"revised \"{md(finding.revised_value) or '—'}\"")
            st.markdown("**Evidence this explanation cites**")
            shown = False
            for ref in item.evidence_refs:
                evidence = resolve_ref(findings, ref)
                if evidence is not None:
                    shown = True
                    where = f"page {evidence.page}" if evidence.page is not None else "page not recorded"
                    st.caption(f"`{ref}` · {evidence.source} label · {where}")
                    st.code(evidence.text, language=None)
            if not shown:
                st.caption("No evidence quote was recorded for this finding.")
    st.markdown("**Questions a qualified reviewer could ask**")
    for question in explanation.questions:
        st.markdown(f"- {md(question)}")
    render_report_download(
        "Download explanation (Markdown)", lambda: explanation_to_markdown(explanation), kind="Explanation report",
        filename="labelguard_explanation.md", mime="text/markdown", key="download_explanation",
    )


def _render_saved(findings: tuple[Finding, ...]) -> None:
    job_id = st.session_state.get("run_id")
    if not job_id:
        return
    try:
        saved = explanation_db.list_explanations(job_id)
    except (sqlite3.Error, OSError):
        return
    if saved:
        with st.expander(f"Explanations saved for this analysis ({len(saved)})"):
            for stored in saved:
                st.markdown(f"**{stored.created_at.replace('T', ' ').replace('+00:00', ' UTC')}** · {stored.explanation.label} · `{stored.explanation_uid}`")
                st.write(stored.explanation.summary)
