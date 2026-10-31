# LabelGuard AI - test report

Run on 2026-10-29 on one Linux development machine (2 logical CPUs). This report says what was run, what the results were,
and what was **not** tested. It does not claim production readiness or regulatory compliance; all documents are fictional.

## 1. Summary

| Configuration | Result |
|---|---|
| Python 3.13, Tesseract installed | **427 passed**, 0 failed, 0 skipped (103 s) |
| Python 3.13, Tesseract hidden from `PATH` | **424 passed**, 3 skipped, 0 failed (70 s) |
| Python 3.11, Tesseract installed | **427 passed**, 0 failed, 0 skipped (112 s) |
| Python 3.11, Tesseract hidden from `PATH` | **424 passed**, 3 skipped, 0 failed (64 s) |
| Python 3.11, brand-new virtual environment built only from `requirements.txt` | **427 passed**, 0 failed (100 s); proves no dependency is missing |
| Browser end-to-end (`scripts/browser_e2e.py`, Chromium), no API key | 15 of 15 checks passed |
| Browser end-to-end, with a deliberately invalid API key (live call, HTTP 401) | 16 of 16 checks passed |
| Synthetic evaluation corpus (`python -m labelguard.evaluation`) | 33 of 33 pairs exact, TP 39 / FP 0 / FN 0 (see `EVALUATION_REPORT.md`) |

The 3 skipped tests are the ones that need the Tesseract program (OCR of a scan): `test_checks_and_pipeline.py`,
`test_ingestion.py` and `test_scenarios.py` (scanned copy *with* OCR). They are skipped by design when it is missing; the
matching "scan without OCR is not verified, never a pass" test runs in both configurations.

Tests that call the Claude API use a local test double; **no paid call and no real key are used by the suite**.

Commands: `python -m pytest -q` (whole suite); `python -m pytest -q -rs` shows the skip reasons.
Not run on Windows (see section 6).

## 2. What the 427 tests cover

| File | Tests | Area |
|---|---|---|
| `test_scenarios.py` | 21 | The 14 required Day 4 scenarios, each with a known expected outcome (section 3) |
| `test_e2e_workflow.py` | 4 | The whole 12-step workflow through the real pages: no key, working key, failing key; plus an unchanged pair |
| `test_security.py` | 23 | Upload validation, limits, temp-file cleanup, error text, secrets, repository hygiene |
| `test_untrusted_output.py` | 27 | Label text, file names and reviewer names treated as data in the UI, reports and CSV |
| `test_ai.py`, `test_ai_injection.py` | 70 | Provider errors, response validation, grounding, fallback, prompt-injection handling |
| `test_database_audit.py` | 35 | Initialization, restart persistence, migration of an old database, review workflow, append-only audit |
| `test_impact.py` | 28 | Impact analysis: related and unrelated labels, filters, rule-file errors |
| `test_day3_pages.py`, `test_app_smoke.py` | 43 | Every page renders; queue, audit, impact and AI pages; navigation |
| `test_report_export.py`, `test_db_and_report.py`, `test_reviews_report_corpus.py` | 23 | Reports, export failure, run log, review persistence |
| `test_verification.py`, `test_rules_and_identifiers.py`, `test_vision.py` | 46 | Rules engine, GTIN/UDI/date checks, artwork diff, barcodes |
| `test_fields.py`, `test_comparison.py`, `test_ingestion.py`, `test_pdf_text.py`, `test_normalize.py`, `test_checks_and_pipeline.py`, `test_samples.py`, `test_startup.py`, `test_evaluation.py` | 107 | Extraction, comparison, ingestion, samples, import checks, the evaluation harness |

## 3. Required scenarios and their expected outcomes

All pass in all four configurations unless a row says otherwise. "Expected" was written before the case was run.

| # | Scenario | Expected outcome | Test (`tests/test_scenarios.py` unless noted) |
|---|---|---|---|
| 1 | Identical label versions | No findings | `test_identical_label_versions_have_no_findings` |
| 2 | Changed product identifier | Flagged; both values quoted as evidence | `test_changed_product_identifier_is_flagged_...` |
| 3 | Removed safety warning | Flagged; the removed text is quoted | `test_removed_safety_warning_is_flagged_...` |
| 4 | Missing configured required field | Flagged; nothing guessed | `test_missing_required_field_...`, `test_label_missing_several_required_fields_...` |
| 5 | Added harmless text | Reported at low priority only | `test_added_harmless_text_is_reported_at_low_priority_only` |
| 6 | Date mismatch | Changed expiry flagged; expiry before manufacture flagged | `test_changed_expiry_date_...`, `test_expiry_before_manufacture_...` |
| 7 | Multi-page PDF | Identical pages clean; an added page is flagged | `test_identical_multi_page_labels_...`, `test_added_page_is_flagged` |
| 8 | Scanned PDF, OCR available | No false identifier or warning findings | `test_scanned_copy_with_ocr_...` (skipped when Tesseract is absent) |
| 9 | Scanned PDF, no OCR | "Not verified", never a pass; no 16 false findings | `test_scanned_copy_without_ocr_is_not_verified_...` |
| 10 | Malformed or encrypted PDF | Refused before processing, with a plain message | `test_malformed_or_encrypted_files_are_rejected_...`, `test_uploading_an_encrypted_file_in_the_app_...` |
| 11 | Page layout change | Flagged by the artwork rule only, not by text rules; "not verified" when vision is off | `test_layout_change_is_flagged_...`, `test_layout_change_is_not_verified_...` |
| 12 | Database persistence | Everything readable after close and reopen; old database upgraded | `test_database_audit.py::test_everything_persists_across_a_restart`, `test_old_day2_database_is_upgraded_in_place` |
| 13 | AI API failure | Deterministic explanation with the reason; findings unchanged | `test_ai.py` (timeout, rate limit, 401, 5xx, network), `test_e2e_workflow.py` (`key_failing`), live 401 in the browser run |
| 14 | Report export failure | An error message, no crash, app keeps working | `test_report_export.py::test_a_failing_report_builder_shows_an_error_but_not_a_crash` |

## 4. The 12-step workflow

`test_e2e_workflow.py::test_the_whole_workflow` runs steps 1-12 through the real Streamlit pages three times (no key; key with a
working test double; key with a failing double), and `scripts/browser_e2e.py` repeats them in Chromium with the real file
uploader and a real report download.

1 start the app · 2-3 upload approved and revised labels · 4 extract · 5 compare · 6 run rules · 7 findings with evidence ·
8 review (a named reviewer; Resolve refused without a rationale, then accepted) · 9 audit history kept, including the resolved
finding's whole path · 10 change-impact analysis (kit flagged, unrelated syringe not) · 11 AI explanation (offered only with a
key; falls back with a stated reason when the call fails) · 12 report export, logged in the audit history.
The test also checks that the AI and review steps change no finding, and that the key is nowhere in the page, the database or
the report.

## 5. Security and reliability checks

| Check | Evidence |
|---|---|
| Files validated before processing (type, size, format, encryption) | `test_security.py::test_an_invalid_upload_is_rejected_before_any_temporary_file_is_created`; refusals listed in `EVALUATION_REPORT.md` (6 of 6) |
| Size and page limits enforced | `test_a_document_just_over_the_page_limit_is_refused_and_one_at_the_limit_is_accepted`; `test_page_and_size_limits_are_enforced` |
| Temporary files removed (success, OCR, failure; stale folders after a killed process) | `test_temp_folders_are_removed_...`, `test_stale_upload_folders_from_a_killed_process_...` |
| Uploads never written into the application folder | `test_the_application_never_writes_uploaded_labels_into_its_folder` |
| Hostile file names never used as paths | `test_a_hostile_file_name_is_never_used_as_a_path` |
| Exceptions do not expose secrets or paths | `test_an_unexpected_error_during_analysis_shows_only_the_error_class`, `test_an_unexpected_error_on_any_page_is_contained` |
| API key only from environment, read in one module | `test_api_keys_are_only_read_from_the_environment_by_one_module` |
| Works without a key | The `no_key` run of the e2e test; the whole suite runs with no key set |
| No secret in logs, `repr`, database, reports or page | `test_ai.py` (sentinel key + `caplog`), `test_security.py`, e2e test, browser runs (server log checked: 0 occurrences) |
| Untrusted label text is data for the model | `test_ai_injection.py`, `test_security.py::test_instructions_hidden_in_a_real_label_pdf_...` |
| AI cannot alter validation results or statuses | `test_the_ai_cannot_change_a_finding`, `test_an_injected_label_cannot_change_findings_or_statuses`, structural test that `ai/` has no database access |
| Untrusted text cannot become an image, link or spreadsheet formula | `test_untrusted_output.py` (found in Chromium during Day 4 exploratory testing, before the fix) |
| Audit events survive status changes; cannot be edited or deleted by the app | `test_database_audit.py` (triggers refuse UPDATE and DELETE) |
| No real patient data or confidential labels in the repository | `test_no_email_addresses_phone_numbers_or_id_numbers_in_the_repository`, `test_every_pdf_in_the_repository_is_one_of_the_synthetic_samples` |

A live check with a deliberately invalid key reached the real API and returned HTTP 401. The adapter mapped it to
"The Claude API rejected the credentials (HTTP 401)", and the key appeared in neither the message nor the logs.

## 6. Defects found during final verification, and how they were resolved

- Two tests were stale after earlier security work, not product defects. `test_report_contains_files_changes_and_evidence`
  expected raw file names, but reports now escape Markdown in untrusted text (`approved\_label\_v1.pdf`); it now expects the
  escaped form. The repository-hygiene scan flagged `mail@evil.example`, a deliberate injection fixture; it now allows the
  reserved `.example` domain only. No assertion was weakened: both tests still check what they were written to check.
- **Findings and Review Queue filters** clipped their selected options when four were shown across; they are now two per row.
  This is a layout change only. Confirmed in Chromium.
- An apparent bug (the finding panel "collapsing" after a refused Resolve) was investigated in the browser and was a mistake
  in the test locator, not in the app: the panel stays open and the error is shown next to the buttons. No code was changed.
- The demo script said the resolved finding stays in the Review Queue's default list; it is hidden by default (the page says
  how many, and it is listed when *Resolved* is chosen). The script is corrected.
- README said 24 test files; there are 25. Corrected.

## 7. Not tested, or tested only partly

- **Windows.** Nothing was executed on Windows (the project targets Windows with Python 3.11); the Windows commands in the
  README follow the same code paths but are unverified there. Python 3.11 was tested on Linux only.
- **A successful live model answer.** Only the live *failure* path (HTTP 401 with an invalid key) was exercised. AI success,
  grounding and rejection are covered with a test double, so real model behaviour, including how often the validator rejects an
  acceptable answer, is unmeasured.
- **Real labels.** Everything uses fictional, generated documents. Behaviour on real artwork, other languages, symbols, tables
  and multi-column text is unknown. The evaluation score is a regression baseline: the same author wrote the labels and the
  ground truth, and the sample is small, so it carries no statistical significance.
- **Scale and concurrency.** Largest document tested: 2 pages. No load, multi-user or large-file tests; timings are single runs.
- **Browsers other than Chromium**, mobile layouts, keyboard and screen-reader accessibility.
- **`scripts/browser_e2e.py`** is optional and not part of `pytest` (it needs Playwright and a running app).
- **Print quality of barcodes**, colour accuracy of artwork, and OCR in languages other than English.

## 8. How to reproduce

```powershell
python -m pytest -q                 # whole suite
python -m pytest -q -rs             # also list skipped tests and why
python -m labelguard.evaluation --write docs\EVALUATION_REPORT.md

# optional browser check (needs: pip install playwright; python -m playwright install chromium)
$env:LABELGUARD_DB_PATH = "$env:TEMP\labelguard_e2e.db"
streamlit run app.py --server.port 8765            # in one window
python scripts\browser_e2e.py --url http://localhost:8765   # in another
```
