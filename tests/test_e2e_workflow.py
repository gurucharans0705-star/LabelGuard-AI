"""The complete 12-step workflow, driven through the real Streamlit pages, with and without API credentials.

No network is used: the Claude provider is replaced by a local double that returns a canned (valid) answer or fails.
"""
import json

import pytest

from labelguard.ai import service
from labelguard.ai.models import AI_ASSISTED, DETERMINISTIC, AiTimeout
from labelguard.db import audit, database
from labelguard.reporting import export
from labelguard.ui import state

from test_app_smoke import new_app, open_page, texts, upload

SENTINEL_KEY = "TEST-SENTINEL-not-a-real-key-0000"


class WorkingClaude:
    """Reads the finding ids and evidence references out of the request and answers with grounded, cautious text."""

    name, model = "claude", "test-double"

    def __init__(self, settings=None):
        pass

    def complete(self, system, user):
        data = json.loads(user.split("\n", 1)[1])
        findings = [{"finding_id": f["finding_id"], "explanation": "The configured rule flagged this field because the two labels differ.",
                     "evidence_refs": [f["evidence"][0]["ref"]] if f["evidence"] else []} for f in data["findings"]]
        return json.dumps({"summary": "The rules flagged differences between the two labels for a reviewer to check.",
                           "findings": findings, "reviewer_questions": ["Is each change covered by an approved change request?"]})


class FailingClaude:
    name, model = "claude", "test-double"

    def __init__(self, settings=None):
        pass

    def complete(self, system, user):
        raise AiTimeout("The Claude API did not answer within 30 seconds.")


@pytest.fixture(params=["no_key", "key_working", "key_failing"])
def credentials(request, monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    if request.param != "no_key":
        monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)
        monkeypatch.setattr(service, "ClaudeProvider", WorkingClaude if request.param == "key_working" else FailingClaude)
    return request.param


def snapshot(result):
    return [(f.id, f.rule_id, f.severity, f.result, f.approved_value, f.revised_value) for f in result.report.findings]


def test_the_whole_workflow(credentials, approved_pdf, revised_pdf):
    # 1. start the application
    at = new_app()
    assert not at.exception
    # 2-3. upload the approved and the revised label
    at = open_page(at, "Upload & Analyze")
    upload(at, "approved", "approved_label_v1.pdf", approved_pdf.read_bytes())
    upload(at, "revised", "revised_label_v2.pdf", revised_pdf.read_bytes())
    assert not at.exception and not at.button(key="start_analysis").disabled
    # 4-7. extract, compare, run the rules, show findings with evidence
    at.button(key="start_analysis").click().run()
    assert not at.exception and at.radio(key="nav").value == "Findings"
    result = at.session_state["verification"]
    assert result.analysis.approved.document.page_count == 1 and "NW-IV200-20" in result.analysis.approved.document.full_text
    assert len(result.report.flagged) == 8
    assert all(f.evidence and f.recommended_action and f.id for f in result.report.flagged)
    before = snapshot(result)
    finding = result.report.flagged[0]

    # 8. review and update statuses (a named person, a rationale to resolve)
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    at.button(key=f"start_{finding.id}").click().run()
    at.text_input(key=f"note_{finding.id}").set_value("matches change request CR-9").run()
    at.button(key=f"res_{finding.id}").click().run()
    assert not at.exception and at.session_state["reviews"][finding.id].status == "resolved"

    # 9. the audit history was saved and still shows the resolved finding's whole path
    trail = [e for e in audit.list_events(entity_id=finding.id) if e.event_type == audit.REVIEW_STATUS_CHANGED]
    assert [e.details["to_status"] for e in trail] == ["resolved", "under_review"]
    assert audit.ANALYSIS_CREATED in [e.event_type for e in audit.list_events()]
    open_page(at, "Audit History")
    assert not at.exception

    # 10. change-impact analysis
    open_page(at, "Change Impact")
    at.button(key="load_demo_catalog").click().run()
    at.button(key="run_impact_analysis").click().run()
    assert not at.exception and at.session_state["impact_results"]["result"].items
    assert audit.IMPACT_ANALYSIS_RUN in [e.event_type for e in audit.list_events()]

    # 11. AI-assisted explanations when configured; the deterministic explanation otherwise or on failure
    open_page(at, "AI Explanations")
    if credentials != "no_key":
        ai_option = next(o for o in at.radio(key="ai_mode").options if o.startswith("AI-assisted"))
        at.radio(key="ai_mode").set_value(ai_option).run()
        at.checkbox(key="ai_confirm").check().run()
    else:
        assert all(not o.startswith("AI-assisted") for o in at.radio(key="ai_mode").options)
    at.button(key="generate_explanation").click().run()
    assert not at.exception
    explanation = at.session_state["explanation"]
    if credentials == "key_working":
        assert explanation.source == AI_ASSISTED and not explanation.note and explanation.model == "test-double"
    else:
        assert explanation.source == DETERMINISTIC and explanation.items
        assert ("did not answer" in explanation.note) == (credentials == "key_failing")  # failure is explained
    assert [i.finding_id for i in explanation.items] == [f.id for f in result.report.findings]

    # the AI step and the review step changed no finding and no other status
    assert snapshot(at.session_state["verification"]) == before
    assert database.review_status_counts()["resolved"] == 1

    # 12. export the verification report (with the review decision in it), and log the download
    report = export.verification_to_markdown(at.session_state["verification"], at.session_state["reviews"])
    assert finding.id in report and "NW-IV200-20" in report and "matches change request CR-9" in report
    assert "decision-support" in report and SENTINEL_KEY not in report
    state.log_report("Verification report", "report.md", report)
    assert audit.REPORT_GENERATED in [e.event_type for e in audit.list_events()]

    # nothing anywhere stored or showed the key
    assert SENTINEL_KEY not in texts(at.markdown) + texts(at.error) + texts(at.warning) + texts(at.caption)
    from contextlib import closing
    import sqlite3
    from labelguard import config
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        assert SENTINEL_KEY not in "\n".join(c.iterdump())


def test_an_unchanged_pair_works_end_to_end_and_says_nothing_about_compliance(approved_pdf):
    at = open_page(new_app(), "Upload & Analyze")
    upload(at, "approved", "a.pdf", approved_pdf.read_bytes())
    upload(at, "revised", "b.pdf", approved_pdf.read_bytes())
    at.button(key="start_analysis").click().run()
    assert not at.exception and at.session_state["verification"].report.flagged == ()
    assert "not a statement that the label is compliant" in texts(at.caption)
    report = export.verification_to_markdown(at.session_state["verification"])
    assert "decision-support" in report
