"""The Day 4 evaluation: extended corpus, F1, robustness cases, timing and the Markdown report."""
import pytest

from labelguard import evaluation as ev


@pytest.fixture(scope="module")
def results():
    return ev.run_corpus(ev.FULL_CORPUS)


def test_metric_arithmetic_including_f1():
    case = ev.Case("c", ev.A, frozenset({"R-1", "R-2", "R-3"}))
    m = ev.compute_metrics([ev.CaseResult(case, frozenset({"R-1", "R-2", "R-9"}))])
    assert (m.tp, m.fp, m.fn) == (2, 1, 1)
    assert m.precision == pytest.approx(2 / 3) and m.recall == pytest.approx(2 / 3) and m.f1 == pytest.approx(2 / 3)
    empty = ev.compute_metrics([])
    assert empty.cases == 0 and (empty.tp, empty.fp, empty.fn) == (0, 0, 0)  # an empty run is defined, not a crash


def test_skipped_cases_do_not_count():
    case = ev.Case("c", ev.A, frozenset({"R-1"}), needs_ocr=True)
    skipped = ev.CaseResult(case, frozenset(), skipped="no OCR")
    m = ev.compute_metrics([skipped])
    assert (m.tp, m.fp, m.fn, m.cases) == (0, 0, 0, 0)


def test_the_extended_corpus_is_exactly_as_the_ground_truth_says(results):
    ran = [r for r in results if not r.skipped]
    assert len(ran) >= 29  # 21 original + 8 extra digital cases (+ OCR cases when OCR is installed)
    wrong = {r.case.name: (sorted(r.false_positives), sorted(r.false_negatives)) for r in ran if r.false_positives or r.false_negatives}
    assert wrong == {}, wrong  # any change in behaviour shows up here, with the case name
    assert ev.compute_metrics(results).f1 == 1.0


def test_scans_must_say_not_verified_and_never_pass(results):
    for r in results:
        if r.case.expected_not_evaluated and not r.skipped:
            assert not r.missing_not_evaluated, (r.case.name, sorted(r.missing_not_evaluated))
    off = next(r for r in results if r.case.name == "scan, OCR switched off")
    assert off.flagged == frozenset() and "R-READ-001" in off.not_evaluated


def test_cases_that_need_ocr_are_skipped_when_it_is_missing(monkeypatch):
    monkeypatch.setattr(ev, "_ocr_available", lambda: False)
    out = ev.run_corpus(ev.OCR_CASES)
    assert all(r.skipped and "OCR" in r.skipped for r in out) and ev.compute_metrics(out).cases == 0


def test_timings_and_page_counts_are_recorded(results):
    for r in results:
        if not r.skipped:
            assert r.seconds is not None and 0 < r.seconds < 120 and min(r.pages) >= 1
    multi = next(r for r in results if r.case.name == "page removed from revised")
    assert multi.pages == (2, 1)


def test_malformed_and_encrypted_documents_are_refused():
    out = ev.run_robustness()
    assert len(out) == 6 and all(r.refused for r in out), [(r.name, r.message) for r in out if not r.refused]


def test_the_markdown_report_states_definitions_limits_and_corrections(results):
    report = ev.format_markdown_report(results, ev.run_robustness())
    for needle in ("Positive finding", "Ground truth", "Precision", "Recall", "F1", "not** an estimate of accuracy on real labels",
                   "no statistical significance", "Corrections to the ground truth", "TP 38, FP 1, FN 3", "Processing time",
                   "6/6 refused"):
        assert needle in report, needle
    assert "certif" not in report.lower() and "production" not in report.lower()  # no overclaiming anywhere in the report


def test_the_report_in_docs_was_generated_by_this_code():
    from pathlib import Path

    text = (Path(__file__).resolve().parents[1] / "docs" / "EVALUATION_REPORT.md").read_text(encoding="utf-8")
    assert "python -m labelguard.evaluation --write docs/EVALUATION_REPORT.md" in text and "Pairs run" in text
