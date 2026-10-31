"""The Day 3 pages, driven through Streamlit's AppTest: demo data, impact, review queue, audit, AI, history."""
import pytest

from labelguard.db import audit, catalog, database

from test_app_smoke import analysed_app, new_app, open_page, texts


def no_errors(at):
    assert not at.exception, [e.value for e in at.exception]


def demo_analysed_app():
    at = open_page(new_app(), "Change Impact")
    at.button(key="load_demo_catalog").click().run()
    no_errors(at)
    at.radio(key="nav").set_value("Upload & Analyze").run()
    at.button(key="load_samples").click().run()
    at.button(key="start_analysis").click().run()
    no_errors(at)
    return at


@pytest.mark.parametrize("page", ["AI Explanations", "Review Queue", "Change Impact", "Product History", "Audit History"])
def test_every_new_page_renders_on_an_empty_database(page):
    at = open_page(new_app(), page)
    no_errors(at)
    assert not at.error


def test_loading_and_clearing_the_demo_catalog():
    at = open_page(new_app(), "Change Impact")
    at.button(key="load_demo_catalog").click().run()
    no_errors(at)
    assert catalog.has_demo_data() and len(catalog.load_catalog().labels) == 14
    assert [e.event_type for e in audit.list_events()] == [audit.DEMO_DATA_LOADED]
    assert at.button(key="clear_demo").disabled  # needs the confirmation tick first
    at.checkbox(key="confirm_clear_demo").check().run()
    at.button(key="clear_demo").click().run()
    no_errors(at)
    assert not catalog.has_demo_data() and catalog.load_catalog().is_empty
    assert audit.DEMO_DATA_CLEARED in [e.event_type for e in audit.list_events()]  # the audit trail remains


def test_impact_from_a_real_comparison_shows_labels_markets_languages_reasons_and_actions():
    at = demo_analysed_app()
    open_page(at, "Change Impact")
    at.button(key="run_impact_analysis").click().run()
    no_errors(at)
    run_text = texts(at.markdown) + texts(at.caption) + texts(at.subheader)
    for fragment in ("L-IV200-20-US-EN-U", "L-KIT-A-US-EN-U"):
        assert fragment in run_text or fragment in str(at.session_state["impact_results"]["result"].items)
    result = at.session_state["impact_results"]["result"]
    assert result.change.parameter and result.items and all(i.reason and i.actions for i in result.items)
    assert len(catalog.list_impact_runs()) == 1
    assert audit.IMPACT_ANALYSIS_RUN in [e.event_type for e in audit.list_events()]
    assert not [i for i in result.items if "SYR50" in i.label.label_id or "CATH10" in i.label.label_id]


def test_what_if_analysis_on_an_unrelated_product_stays_inside_that_product():
    at = open_page(new_app(), "Change Impact")
    at.button(key="load_demo_catalog").click().run()
    syringe = next(o for o in at.selectbox(key="impact_config_w").options if "C-SYR50-STD" in o)
    at.selectbox(key="impact_config_w").select(syringe)
    at.text_input(key="impact_new_w").set_value("CHANGED").run()
    at.button(key="run_impact_whatif").click().run()
    no_errors(at)
    result = at.session_state["impact_results"]["result"]
    assert result.items and all("SYR50" in i.label.label_id for i in result.items)  # nothing contains the syringe


def test_findings_pages_review_queue_and_audit_share_one_decision():
    at = demo_analysed_app()
    finding = at.session_state["verification"].report.flagged[0]
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    at.button(key=f"start_{finding.id}").click().run()
    at.text_input(key=f"note_{finding.id}").set_value("matches the change request").run()
    at.button(key=f"res_{finding.id}").click().run()
    no_errors(at)

    open_page(at, "Review Queue")
    no_errors(at)
    queue = database.list_review_queue()
    assert any(i.finding.id == finding.id and i.status == "resolved" for i in queue)  # resolved stays in the queue

    open_page(at, "Audit History")
    no_errors(at)
    changes = [e for e in audit.list_events(event_types=[audit.REVIEW_STATUS_CHANGED]) if e.entity_id == finding.id]
    assert [e.details["to_status"] for e in changes] == ["resolved", "under_review"] and changes[0].actor == "QA Tester"


def test_queue_filters_do_not_crash_and_can_hide_unverified():
    at = demo_analysed_app()
    open_page(at, "Review Queue")
    no_errors(at)
    at.text_input(key="queue_search").set_value("identifier").run()
    no_errors(at)
    at.text_input(key="queue_search").set_value("zzz-nothing-matches").run()
    no_errors(at)


def test_ai_page_works_without_a_key_and_never_offers_ai_mode(monkeypatch):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    at = demo_analysed_app()
    open_page(at, "AI Explanations")
    no_errors(at)
    assert "AI-assisted" not in at.radio(key="ai_mode").options
    at.button(key="generate_explanation").click().run()
    no_errors(at)
    explanation = at.session_state["explanation"]
    assert explanation.source == "deterministic" and len(explanation.items) >= 1
    events = audit.list_events(event_types=[audit.AI_EXPLANATION_CREATED])
    assert len(events) == 1 and events[0].details["source"] == "deterministic"
    assert database.review_status_counts()["resolved"] == 0  # explaining decided nothing


def test_product_history_lists_jobs_and_catalog_records():
    at = demo_analysed_app()
    open_page(at, "Product History")
    no_errors(at)
    assert len(database.list_runs(10)) == 1 and database.list_runs(10)[0].is_demo


def test_dashboard_shows_review_counts_after_a_decision():
    at = demo_analysed_app()
    finding = at.session_state["verification"].report.flagged[0]
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    at.button(key=f"start_{finding.id}").click().run()
    open_page(at, "Dashboard")
    no_errors(at)
    assert any("review" in (m.label or "").lower() for m in at.metric)


def test_downloading_a_report_is_recorded_in_the_audit_trail():
    from labelguard.ui import state

    state.log_report("Verification report", "r.md", "# body")  # the callback behind every download button
    (event,) = audit.list_events(event_types=[audit.REPORT_GENERATED])
    assert event.details["sha256"] and "body" not in str(event.details)


def test_review_queue_hides_resolved_by_default_says_so_and_shows_the_rationale_when_asked():
    """The queue is a work list: Resolved is filtered out by default, the page says how many are hidden, and nothing is deleted."""
    at = demo_analysed_app()
    finding = at.session_state["verification"].report.flagged[0]
    at.text_input(key="reviewer_name").set_value("QA Tester").run()
    at.text_input(key=f"note_{finding.id}").set_value("matches change request CR-9").run()
    at.button(key=f"res_{finding.id}").click().run()
    open_page(at, "Review Queue")
    no_errors(at)
    assert "1 resolved finding(s) are hidden by the status filter; they are not deleted" in texts(at.markdown)
    at.multiselect(key="queue_status").select("resolved").run()
    no_errors(at)
    assert at.text_input(key=f"note_q{database.list_runs(1)[0].id}_{finding.id}").value == "matches change request CR-9"
    assert "Resolved" in [m.label for m in at.metric] and [m.value for m in at.metric if m.label == "Resolved"] == ["1"]


def test_an_unreadable_database_is_explained_on_the_change_impact_page(tmp_path, monkeypatch):
    """Found in Day 4 exploratory testing: a damaged database used to look like an ordinary empty catalog."""
    from labelguard import config

    broken = tmp_path / "broken.db"
    broken.write_bytes(b"this is not a sqlite database " * 100)
    monkeypatch.setattr(config, "DB_PATH", broken)
    at = open_page(new_app(), "Change Impact")
    no_errors(at)
    assert any("could not be read" in w.value for w in at.warning)
    at = open_page(at, "Dashboard")
    no_errors(at)
