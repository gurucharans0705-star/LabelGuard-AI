"""Report export: it must fail safely (a broken report never takes the page down) and every export is audited."""
import pytest

from labelguard.db import audit
from labelguard.reporting import export

from test_app_smoke import analysed_app, open_page, texts


@pytest.mark.parametrize("page, builder", [
    ("Findings", "verification_to_markdown"),
    ("Results", "comparison_to_markdown"),
])
def test_a_failing_report_builder_shows_an_error_but_not_a_crash(monkeypatch, page, builder):
    at = analysed_app()
    monkeypatch.setattr(export, builder, lambda *a, **k: (_ for _ in ()).throw(RuntimeError("boom /secret/path")))
    import labelguard.ui.findings as findings_page
    import labelguard.ui.results as results_page

    for module in (findings_page, results_page):
        if hasattr(module, builder):
            monkeypatch.setattr(module, builder, getattr(export, builder))
    open_page(at, page)
    assert not at.exception, [e.value for e in at.exception]
    assert "report could not be created" in texts(at.error).lower()
    assert "secret/path" not in texts(at.error)  # no internal detail in the message
    if page == "Findings":
        assert len(at.session_state["verification"].report.findings) > 0 and at.radio(key="nav").value == "Findings"


def test_every_report_download_is_written_to_the_audit_trail():
    from labelguard.ui import state

    for kind, name in [("Verification report", "v.md"), ("Comparison report", "c.md"), ("Impact report", "i.md"),
                       ("Explanation report", "e.md"), ("Audit history export", "a.csv")]:
        state.log_report(kind, name, f"content of {kind}")
    events = audit.list_events(event_types=[audit.REPORT_GENERATED])
    assert len(events) == 5 and all(e.details["sha256"] for e in events)
