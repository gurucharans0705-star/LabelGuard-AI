import sqlite3
from dataclasses import replace

import pytest

from labelguard import config, evaluation
from labelguard.db import database
from labelguard.reporting.export import verification_to_markdown
from labelguard.verification import verify_pair

H1, H2 = "a" * 64, "b" * 64


def test_review_set_get_history_and_upsert():
    assert database.get_reviews(H1, H2) == {}
    database.set_review(H1, H2, "F-1", "R-ID-001", "critical_review", "under_review", "looking", "QA")
    record = database.set_review(H1, H2, "F-1", "R-ID-001", "critical_review", "resolved", "intended change", "QA")
    assert record.reviewer == "QA"
    reviews = database.get_reviews(H1, H2)
    assert reviews["F-1"].status == "resolved" and reviews["F-1"].rationale == "intended change"
    history = database.review_history(H1, H2, "F-1")
    assert [(e.from_status, e.to_status) for e in history] == [("pending", "under_review"), ("under_review", "resolved")]
    assert database.get_reviews(H2, H1) == {}  # decisions belong to this exact pair of files


def test_bad_review_status_is_rejected():
    with pytest.raises(ValueError):
        database.set_review(H1, H2, "F-1", "R", "low", "approved", "", "QA")


def test_old_day1_database_is_upgraded_in_place(tmp_path):
    path = tmp_path / "old.db"
    with sqlite3.connect(path) as connection:
        connection.execute(
            "CREATE TABLE analysis_runs (id INTEGER PRIMARY KEY AUTOINCREMENT, created_at TEXT, approved_filename TEXT,"
            " approved_sha256 TEXT, revised_filename TEXT, revised_sha256 TEXT, fields_changed INTEGER,"
            " text_modified INTEGER, text_added INTEGER, text_removed INTEGER, ocr_pages INTEGER)"
        )
    assert database.list_runs(5, db_path=path) == []
    database.set_review(H1, H2, "F-1", "R", "low", "resolved", "checked", "QA", db_path=path)
    assert "F-1" in database.get_reviews(H1, H2, db_path=path)


def test_full_label_text_is_not_stored(approved_pdf, revised_pdf):
    result = verify_pair((approved_pdf.read_bytes(), "a.pdf"), (revised_pdf.read_bytes(), "r.pdf"))
    database.save_run(result.analysis, report=result.report)
    with sqlite3.connect(config.DB_PATH) as connection:
        dump = "\n".join(connection.iterdump())
    assert result.analysis.approved.document.full_text not in dump
    assert result.analysis.revised.document.full_text not in dump


def test_verification_report_contents(approved_pdf, revised_pdf):
    result = verify_pair((approved_pdf.read_bytes(), "a.pdf"), (revised_pdf.read_bytes(), "r.pdf"))
    first = result.report.findings[0]
    record = database.set_review(
        result.analysis.approved.document.sha256, result.analysis.revised.document.sha256,
        first.id, first.rule_id, first.severity, "under_review", "checked with QA", "QA",
    )
    text = verification_to_markdown(result, {first.id: record})
    assert "workflow priority" in text and "not a regulatory" in text.lower()
    assert first.id in text and "checked with QA" in text and "Recommended" in text
    assert "certif" in text.lower() and "does not" in text.lower()  # the disclaimer is present, not a claim


# --- synthetic corpus -------------------------------------------------------------------------------
@pytest.fixture(scope="module")
def corpus_results():
    return evaluation.run_corpus()


def test_corpus_has_known_expected_outcomes_for_every_case(corpus_results):
    assert len(corpus_results) >= 20
    assert any(not r.case.expected for r in corpus_results)  # includes cases that must give no findings


def test_corpus_precision_and_recall_floors(corpus_results):
    metrics = evaluation.compute_metrics(corpus_results)
    report = evaluation.format_report(corpus_results)
    assert metrics.precision >= 0.95, report
    assert metrics.recall >= 0.95, report
    assert metrics.exact_cases == metrics.cases, report


def test_metric_arithmetic_is_right():
    case = evaluation.Case("c", evaluation.A, frozenset({"R-1", "R-2"}))
    result = evaluation.CaseResult(case, frozenset({"R-1", "R-9"}))
    metrics = evaluation.compute_metrics([result])
    assert (metrics.tp, metrics.fp, metrics.fn) == (1, 1, 1)
    assert metrics.precision == 0.5 and metrics.recall == 0.5
