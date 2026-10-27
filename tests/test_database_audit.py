"""Persistence, review workflow and audit trail (Day 3)."""
import sqlite3
from contextlib import closing

import pytest

from labelguard import config
from labelguard.db import audit, catalog, database
from labelguard.db.schema import SCHEMA_VERSION

H1, H2 = "a" * 64, "b" * 64
TABLES = {
    "analysis_runs", "analysis_findings", "extracted_fields", "finding_reviews", "review_events", "audit_events",
    "products", "product_configurations", "config_parameters", "product_relationships", "label_versions",
    "label_required_fields", "label_review_records", "markets", "languages", "packaging_levels", "impact_runs",
    "impact_items", "ai_explanations",
}


def tables(path=None):
    with closing(database.connect(path)) as c:
        return {r[0] for r in c.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def events(**kw):
    return audit.list_events(**kw)


# --- initialization and restart ------------------------------------------------------------------------------
def test_database_initialises_every_table_and_version(tmp_path):
    path = tmp_path / "fresh.db"
    assert not path.exists()
    assert TABLES <= tables(path)
    with closing(sqlite3.connect(path)) as c:
        assert c.execute("PRAGMA user_version").fetchone()[0] == SCHEMA_VERSION


def test_opening_twice_is_harmless(tmp_path):
    path = tmp_path / "x.db"
    database.set_review(H1, H2, "F-1", "R-1", "low", "under_review", "", "QA", db_path=path)
    tables(path), tables(path)
    assert database.get_reviews(H1, H2, db_path=path)["F-1"].status == "under_review"


def test_everything_persists_across_a_restart(sample_verification, tmp_path):
    """Close every connection, drop all objects, and read the file again as a new process would."""
    path = tmp_path / "restart.db"
    result = sample_verification
    job_id = database.save_run(result.analysis, db_path=path, report=result.report)
    first = result.report.flagged[0]
    database.set_review(result.analysis.approved.document.sha256, result.analysis.revised.document.sha256,
                        first.id, first.rule_id, first.severity, "resolved", "intended change", "J. Smith", db_path=path)
    catalog.load_demo_data(path)
    del result, first

    run = database.get_run(job_id, path)
    assert run.approved_filename == "approved_label_v1.pdf" and run.findings_flagged == 8 and run.job_uid.startswith("JOB-")
    stored = database.load_job_findings(job_id, path)
    assert len(stored) == 8 and stored[0].evidence_complete and stored[0].recommended_action
    fields = database.load_job_fields(job_id, path)
    assert fields["approved"]["product_identifier"] == ["NW-IV200-20"] and fields["revised"]["product_identifier"] == ["NW-IV200-15"]
    queue = database.list_review_queue(db_path=path)
    assert sum(i.status == "resolved" for i in queue) == 1
    assert not catalog.load_catalog(path).is_empty
    assert {e.event_type for e in events(**{"db_path": path})} >= {audit.ANALYSIS_CREATED, audit.REVIEW_STATUS_CHANGED, audit.DEMO_DATA_LOADED}


def test_pdf_bytes_and_full_label_text_are_not_stored(sample_verification):
    """Extracted field values and evidence quotes ARE stored (the spec asks for them); the document itself is not."""
    database.save_run(sample_verification.analysis, report=sample_verification.report)
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        dump = "\n".join(c.iterdump())
    assert "%PDF" not in dump
    for side in (sample_verification.analysis.approved, sample_verification.analysis.revised):
        full = side.ingestion.document.full_text
        assert len(full) > 100 and full not in dump  # whole-page text is never kept
        assert side.ingestion.document.sha256 in dump  # only a safe identifier (hash) and the file name


def test_old_day2_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "day2.db"
    with closing(sqlite3.connect(path)) as c:
        c.executescript(
            "CREATE TABLE analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, approved_filename TEXT,"
            " approved_sha256 TEXT, revised_filename TEXT, revised_sha256 TEXT, fields_matching INTEGER, fields_changed INTEGER,"
            " fields_undetected INTEGER, text_modified INTEGER, text_added INTEGER, text_removed INTEGER, ocr_pages INTEGER,"
            " findings_flagged INTEGER DEFAULT 0, findings_critical INTEGER DEFAULT 0, findings_not_evaluated INTEGER DEFAULT 0);"
            "CREATE TABLE finding_reviews (approved_sha256 TEXT, revised_sha256 TEXT, finding_id TEXT, rule_id TEXT, severity TEXT,"
            " status TEXT, note TEXT DEFAULT '', updated_at TEXT, PRIMARY KEY (approved_sha256, revised_sha256, finding_id));"
            "CREATE TABLE review_events (id INTEGER PRIMARY KEY AUTOINCREMENT, approved_sha256 TEXT, revised_sha256 TEXT,"
            " finding_id TEXT, rule_id TEXT, from_status TEXT, to_status TEXT, note TEXT DEFAULT '', at TEXT);"
            "INSERT INTO analysis_runs VALUES (1,'2026-01-01T00:00:00+00:00','a.pdf','" + H1 + "','b.pdf','" + H2 + "',1,2,3,0,0,0,0,0,0,0);"
            "INSERT INTO finding_reviews VALUES ('" + H1 + "','" + H2 + "','F-1','R','low','open','old note','2026-01-02T00:00:00+00:00');"
            "INSERT INTO finding_reviews VALUES ('" + H1 + "','" + H2 + "','F-2','R','low','acknowledged','','2026-01-02T00:00:00+00:00');"
            "INSERT INTO finding_reviews VALUES ('" + H1 + "','" + H2 + "','F-3','R','low','resolved','done','2026-01-02T00:00:00+00:00');"
        )
    runs = database.list_runs(5, path)
    assert runs[0].job_uid == "JOB-LEGACY-1" and runs[0].fields_changed == 2  # nothing lost, a job id was assigned
    reviews = database.get_reviews(H1, H2, path)
    assert (reviews["F-1"].status, reviews["F-2"].status, reviews["F-3"].status) == ("pending", "under_review", "resolved")
    assert reviews["F-1"].rationale == "old note" and reviews["F-1"].reviewer == ""
    assert TABLES <= tables(path)


# --- review workflow -------------------------------------------------------------------------------------------
@pytest.mark.parametrize("status", ["pending", "under_review", "escalated", "resolved"])
def test_every_status_can_be_set_with_a_named_reviewer(status):
    record = database.set_review(H1, H2, "F-1", "R-1", "high", status, "reason given", "QA")
    assert record.status == status and record.reviewer == "QA" and record.updated_at.endswith("+00:00")


def test_status_labels_match_the_spec():
    assert database.REVIEW_STATUS_LABELS == {
        "pending": "Pending review", "under_review": "Under review", "escalated": "Escalated", "resolved": "Resolved"}


@pytest.mark.parametrize("status", ["resolved", "escalated"])
def test_rationale_is_required_to_resolve_or_escalate(status):
    with pytest.raises(ValueError, match="rationale"):
        database.set_review(H1, H2, "F-1", "R", "low", status, "   ", "QA")
    assert database.get_reviews(H1, H2) == {}  # a refused change leaves no trace in the decisions
    assert audit.list_events(event_types=[audit.REVIEW_STATUS_CHANGED]) == []


def test_rationale_is_not_required_to_start_a_review_or_reopen():
    database.set_review(H1, H2, "F-1", "R", "low", "under_review", "", "QA")
    database.set_review(H1, H2, "F-1", "R", "low", "pending", "", "QA")


@pytest.mark.parametrize("name", ["", "   ", "AI", "Claude", "claude", "assistant", "LLM", "system", "x" * 61])
def test_only_a_named_person_can_decide(name):
    with pytest.raises(ValueError):
        database.set_review(H1, H2, "F-1", "R", "low", "resolved", "ok", name)
    assert database.get_reviews(H1, H2) == {}


def test_unknown_status_and_overlong_rationale_are_rejected():
    with pytest.raises(ValueError, match="Unknown review status"):
        database.set_review(H1, H2, "F-1", "R", "low", "approved", "ok", "QA")
    with pytest.raises(ValueError, match="too long"):
        database.set_review(H1, H2, "F-1", "R", "low", "resolved", "x" * 501, "QA")


def test_there_is_no_approved_or_released_status():
    assert not {"approved", "released", "release", "approve"} & set(database.REVIEW_STATUSES)


def test_a_resolved_finding_stays_in_history_queue_and_audit_trail(sample_verification):
    result = sample_verification
    job_id = database.save_run(result.analysis, report=result.report)
    finding = result.report.flagged[0]
    a, r = result.analysis.approved.document.sha256, result.analysis.revised.document.sha256
    database.set_review(a, r, finding.id, finding.rule_id, finding.severity, "under_review", "", "QA")
    database.set_review(a, r, finding.id, finding.rule_id, finding.severity, "escalated", "needs the quality lead", "QA")
    database.set_review(a, r, finding.id, finding.rule_id, finding.severity, "resolved", "change request CR-12 covers it", "QA")
    assert database.get_reviews(a, r)[finding.id].status == "resolved"
    history = database.review_history(a, r, finding.id)
    assert [(e.from_status, e.to_status) for e in history] == [
        ("pending", "under_review"), ("under_review", "escalated"), ("escalated", "resolved")]
    assert history[-1].rationale == "change request CR-12 covers it" and all(e.reviewer == "QA" for e in history)
    queue = {i.finding.id: i for i in database.list_review_queue()}
    assert queue[finding.id].status == "resolved" and queue[finding.id].rationale.startswith("change request")
    assert len(database.load_job_findings(job_id)) == len(result.report.findings)  # the finding itself is untouched
    trail = [e for e in events(event_types=[audit.REVIEW_STATUS_CHANGED]) if e.entity_id == finding.id]
    assert len(trail) == 3 and trail[0].details["to_status"] == "resolved" and trail[0].actor == "QA"
    # reopening does not erase the earlier history either
    database.set_review(a, r, finding.id, finding.rule_id, finding.severity, "pending", "", "QA")
    assert len(database.review_history(a, r, finding.id)) == 4
    assert len([e for e in events(event_types=[audit.REVIEW_STATUS_CHANGED]) if e.entity_id == finding.id]) == 4


def test_decisions_belong_to_one_pair_of_files():
    database.set_review(H1, H2, "F-1", "R", "low", "resolved", "ok", "QA")
    assert database.get_reviews(H2, H1) == {}
    assert database.review_status_counts() == {"pending": 0, "under_review": 0, "escalated": 0, "resolved": 0}


def test_queue_counts_by_status(sample_verification):
    result = sample_verification
    database.save_run(result.analysis, report=result.report)
    a, r = result.analysis.approved.document.sha256, result.analysis.revised.document.sha256
    f1, f2, f3 = result.report.flagged[:3]
    database.set_review(a, r, f1.id, f1.rule_id, f1.severity, "resolved", "ok", "QA")
    database.set_review(a, r, f2.id, f2.rule_id, f2.severity, "escalated", "ask lead", "QA")
    database.set_review(a, r, f3.id, f3.rule_id, f3.severity, "under_review", "", "QA")
    assert database.review_status_counts() == {"pending": 5, "under_review": 1, "escalated": 1, "resolved": 1}


# --- audit trail -------------------------------------------------------------------------------------------------
def test_saving_an_analysis_creates_one_audit_event(sample_verification):
    database.save_run(sample_verification.analysis, report=sample_verification.report)
    (event,) = events(event_types=[audit.ANALYSIS_CREATED])
    assert event.entity_type == "analysis_job" and event.job_uid.startswith("JOB-") and event.actor == "system"
    assert "8 flagged" in event.summary and event.at.endswith("+00:00") and event.event_uid.startswith("EVT-")
    assert "approved_label_v1.pdf" in event.summary and event.details["approved_sha256"]


def test_audit_events_cannot_be_edited_or_deleted():
    audit.log_event(audit.REPORT_GENERATED, "report", "r.md", summary="one")
    with closing(database.connect()) as c:
        with pytest.raises(sqlite3.Error, match="append-only"):
            c.execute("UPDATE audit_events SET summary = 'changed'")
        with pytest.raises(sqlite3.Error, match="append-only"):
            c.execute("DELETE FROM audit_events")
    assert len(events()) == 1 and events()[0].summary == "one"


def test_report_generation_is_audited_without_storing_the_report():
    from labelguard import workflow

    workflow.record_report_generated("Verification report", "v.md", "# secret report body", actor="QA")
    (event,) = events(event_types=[audit.REPORT_GENERATED])
    assert event.actor == "QA" and event.details["bytes"] == len("# secret report body") and len(event.details["sha256"]) == 64
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        assert "secret report body" not in "\n".join(c.iterdump())


def test_audit_listing_filters_and_orders_newest_first():
    for i in range(3):
        audit.log_event(audit.REPORT_GENERATED, "report", f"r{i}", summary=f"report {i}")
    audit.log_event(audit.IMPACT_ANALYSIS_RUN, "impact_run", "IMP-1", summary="impact")
    assert [e.summary for e in events(event_types=[audit.REPORT_GENERATED])] == ["report 2", "report 1", "report 0"]
    assert [e.entity_id for e in events(entity_id="IMP-1")] == ["IMP-1"]
    assert len(events(limit=2)) == 2


def test_a_failed_change_writes_neither_the_decision_nor_the_event(monkeypatch):
    def broken(*args, **kwargs):
        raise sqlite3.OperationalError("disk full")

    monkeypatch.setattr(audit, "record", broken)
    with pytest.raises(sqlite3.OperationalError):
        database.set_review(H1, H2, "F-1", "R", "low", "resolved", "ok", "QA")
    monkeypatch.undo()
    assert database.get_reviews(H1, H2) == {} and database.review_history(H1, H2) == []  # one transaction: all or nothing


# --- demonstration data --------------------------------------------------------------------------------------------
def test_demo_catalog_loads_once_and_reports_what_it_holds():
    assert not catalog.has_demo_data()
    assert catalog.load_demo_data() == 4 and catalog.has_demo_data()
    catalog.load_demo_data()  # idempotent: no duplicates
    loaded = catalog.load_catalog()
    assert len(loaded.products) == 4 and len(loaded.labels) == 14 and len(loaded.markets) == 4
    assert len(loaded.review_records) == 7 and loaded.label("L-IV200-20-US-EN-U").required_fields


def test_clearing_demo_data_removes_demo_records_but_keeps_the_audit_trail(sample_verification):
    catalog.load_demo_data()
    demo_job = database.save_run(sample_verification.analysis, report=sample_verification.report, is_demo=True)
    real_job = database.save_run(sample_verification.analysis, report=sample_verification.report, is_demo=False)
    f = sample_verification.report.flagged[0]
    a, r = sample_verification.analysis.approved.document.sha256, sample_verification.analysis.revised.document.sha256
    database.set_review(a, r, f.id, f.rule_id, f.severity, "resolved", "ok", "QA")

    summary = catalog.clear_demo_data()
    assert (summary.products, summary.labels, summary.analyses) == (4, 14, 1)
    assert catalog.load_catalog().is_empty and not catalog.has_demo_data()
    assert database.get_run(demo_job) is None and database.get_run(real_job) is not None
    assert database.load_job_findings(demo_job) == () and len(database.load_job_findings(real_job)) == 8
    kinds = [e.event_type for e in events()]
    assert kinds.count(audit.REVIEW_STATUS_CHANGED) == 1 and audit.DEMO_DATA_CLEARED in kinds  # history survives the clear
    assert kinds.count(audit.ANALYSIS_CREATED) == 2


def test_clearing_when_nothing_was_loaded_is_safe():
    summary = catalog.clear_demo_data()
    assert (summary.products, summary.analyses) == (0, 0)
