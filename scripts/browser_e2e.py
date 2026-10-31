"""Optional browser end-to-end check of the 12-step workflow (not part of the pytest suite).

It drives the REAL running app in Chromium with the real file uploader, and checks the page, not the code.

Needs Playwright, which is NOT in requirements.txt:
    pip install playwright
    python -m playwright install chromium

Usage (PowerShell; start the app first in another window with a throw-away database so old decisions do not interfere):
    $env:LABELGUARD_DB_PATH = "$env:TEMP\\labelguard_e2e.db"; streamlit run app.py --server.port 8765
    python scripts\\browser_e2e.py --url http://localhost:8765            # app started WITHOUT an API key
    python scripts\\browser_e2e.py --url http://localhost:8765 --with-key  # app started WITH any key set (see below)

--with-key expects an AI-assisted option on the AI page and then a *failed* live call (an invalid key gives HTTP 401), so use a
deliberately invalid key such as "TEST-SENTINEL-invalid-key-0000". Never use a real key for this check. Prints PASS/FAIL per
check and exits 1 if any failed.
"""
from __future__ import annotations

import argparse
import re
import sys
from pathlib import Path

from playwright.sync_api import sync_playwright

SAMPLES = Path(__file__).resolve().parent.parent / "data" / "samples"


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--url", default="http://localhost:8501")
    parser.add_argument("--with-key", action="store_true", help="the app was started with an (invalid) ANTHROPIC_API_KEY")
    parser.add_argument("--shots", default="e2e_screenshots", help="folder for screenshots")
    args = parser.parse_args()
    shots = Path(args.shots)
    shots.mkdir(exist_ok=True)
    results: list[tuple[str, bool]] = []
    page_errors: list[str] = []

    def check(name: str, condition: bool) -> None:
        results.append((name, bool(condition)))
        print(("PASS " if condition else "FAIL ") + name)

    with sync_playwright() as p:
        browser = p.chromium.launch()
        page = browser.new_context(viewport={"width": 1400, "height": 1000}, accept_downloads=True).new_page()
        page.on("pageerror", lambda e: page_errors.append(str(e)))
        page.goto(args.url)
        page.wait_for_selector("text=LabelGuard", timeout=60000)
        page.wait_for_timeout(1500)

        def no_exception() -> bool:
            return page.locator("[data-testid='stException']").count() == 0

        def go(name: str) -> bool:
            page.get_by_text(name, exact=True).first.click()
            page.wait_for_timeout(1800)
            return no_exception()

        check("1 the app starts and the dashboard shows no exception", no_exception() and page.get_by_text("Approved label").count() > 0)
        go("Upload & Analyze")
        boxes = page.locator("input[type=file]")
        check("two upload boxes", boxes.count() == 2)
        boxes.nth(0).set_input_files(str(SAMPLES / "approved_label_v1.pdf"))
        page.wait_for_timeout(2500)
        boxes.nth(1).set_input_files(str(SAMPLES / "revised_label_v2.pdf"))
        page.wait_for_timeout(2500)
        start = page.get_by_role("button", name="Start label analysis")
        check("2-3 both PDFs accepted and Start is enabled", start.is_enabled())
        start.click()
        page.wait_for_selector("text=Discrepancies flagged", timeout=90000)
        page.wait_for_timeout(2500)
        page.screenshot(path=str(shots / "findings.png"))
        check("4-7 findings with evidence are shown", no_exception() and page.get_by_text("NW-IV200-20").count() > 0)

        page.get_by_role("textbox", name="Reviewer (name or initials)").fill("QA Tester")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1500)
        page.locator("[data-testid='stExpander'] summary").first.click()
        page.wait_for_timeout(900)
        page.get_by_role("button", name="Start review", exact=True).first.click()
        page.wait_for_timeout(1500)
        page.get_by_role("button", name="Resolve", exact=True).first.click()
        page.wait_for_timeout(1500)
        check("8 Resolve without a rationale is refused and the error is shown", page.get_by_text("A short rationale is required").count() > 0)
        page.get_by_role("textbox", name=re.compile("Short rationale")).first.fill("covered by CR-7")
        page.keyboard.press("Enter")
        page.wait_for_timeout(1200)
        page.get_by_role("button", name="Resolve", exact=True).first.click()
        page.wait_for_timeout(1800)
        check("8 the finding shows Status: Resolved and the reviewer",
              page.get_by_text("Status: Resolved", exact=False).count() > 0 and page.get_by_text("by QA Tester", exact=False).count() > 0)

        with page.expect_download(timeout=30000) as download:
            page.get_by_role("button", name=re.compile("Download verification report")).first.click()
        report = Path(download.value.path()).read_text(encoding="utf-8")
        check("12 the report downloads with the notice, the evidence and the decision",
              "decision-support" in report and "NW-IV200-20" in report and "covered by CR-7" in report)
        check("the report contains no API key text", "TEST-SENTINEL" not in report and "sk-ant-" not in report)

        go("Audit History")
        page.screenshot(path=str(shots / "audit.png"))
        check("9 the audit history shows the status change and the report download",
              page.get_by_text("Review status changed").count() > 0 and page.get_by_text("Report generated").count() > 0)
        check("the review queue renders", go("Review Queue"))
        go("Change Impact")
        load = page.get_by_role("button", name=re.compile("Load synthetic demo catalog"))
        if load.count():
            load.click()
            page.wait_for_timeout(2500)
        page.get_by_role("button", name="Analyze impact of this change").click()
        page.wait_for_timeout(3500)
        page.screenshot(path=str(shots / "impact.png"))
        check("10 impact flags the kit label and not the unrelated syringe",
              page.get_by_text("L-KIT-A", exact=False).count() > 0 and page.get_by_text("L-SYR50", exact=False).count() == 0)

        go("AI Explanations")
        ai_option = page.get_by_text(re.compile(r"^AI-assisted"))
        if args.with_key:
            check("11 AI mode is offered when a key is set", ai_option.count() > 0)
            ai_option.first.click()
            page.wait_for_timeout(800)
            page.get_by_text(re.compile("I confirm", re.I)).first.click()
            page.wait_for_timeout(800)
        else:
            check("11 AI mode is NOT offered without a key", ai_option.count() == 0)
        page.get_by_role("button", name=re.compile("Generate", re.I)).first.click()
        page.wait_for_timeout(9000)
        page.screenshot(path=str(shots / "ai.png"))
        body = page.inner_text("body")
        check("11 an explanation is shown and the page has no exception", no_exception() and "Deterministic" in body)
        if args.with_key:
            check("11 a failed API call is explained and falls back to the deterministic text",
                  "rejected the credentials" in body or "unavailable" in body.lower())
        check("no key text anywhere on the page", "TEST-SENTINEL" not in body and "sk-ant-" not in body)
        check("no JavaScript page errors", not page_errors)
        browser.close()

    failed = [name for name, ok in results if not ok]
    print(f"\n{len(results) - len(failed)}/{len(results)} checks passed" + (f"; FAILED: {failed}" if failed else ""))
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())
