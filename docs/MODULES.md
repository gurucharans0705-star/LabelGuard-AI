# Module guide

One line per module, taken from what the code says it does. "Pure" means it has no Streamlit and no database code.

## Entry points
| File | Purpose |
|---|---|
| `app.py` | Streamlit entry point: sidebar navigation, page routing, and a guard that turns an unexpected page error into a short message. |
| `labelguard/config.py` | Paths, limits (upload size, pages), OCR and database settings, all from environment variables with safe defaults. |
| `labelguard/version.py` | The application version, defined in one place. |

## Reading a label (pure)
| Module | Purpose |
|---|---|
| `ingestion.py` | Validate an upload (type, size, format, encryption, page limit), read it in a private temporary folder that is always removed, fall back to OCR for pages without text, and write plain-language notes. |
| `extraction/pdf_text.py` | PyMuPDF text, positioned lines, page metadata, and the checks that refuse corrupt, encrypted or oversized files. |
| `extraction/ocr.py` | Optional Tesseract OCR (pytesseract + Pillow). Reports problems instead of raising. |
| `extraction/normalize.py` | Removes extraction noise without changing meaning (keeps symbols such as degree and plus-minus). |
| `extraction/pdf_render.py` | Renders pages to PNG for preview, OCR, artwork comparison and barcodes. |
| `fields/extractor.py` | Rule-based extraction of product name, REF, UDI, lot, dates, warnings, storage, sterilization and manufacturer, each with its evidence text, page and how it was found. |
| `fields/language.py`, `fields/models.py` | A small stop-word language guess (says "not detected" when unsure) and the field data classes. |

## Comparing and checking (pure)
| Module | Purpose |
|---|---|
| `comparison/engine.py` | Field-by-field and line-by-line comparison: added, removed, modified, moved. |
| `validation/checks.py`, `validation/identifiers.py` | Completeness observations; GTIN check digit, UDI extraction and label date parsing. |
| `vision/compare.py`, `vision/images.py` | Page-by-page artwork comparison with OpenCV (imported lazily). |
| `vision/barcode.py` | Barcode decoding with zxing-cpp; a barcode counts as decoded only if the decoder returned a value. |
| `rules/loader.py` | Strict loader for `config/rules.toml`: any mistake is reported with the rule id. |
| `rules/engine.py` | Turns comparison results into findings with evidence; includes the built-in readability rule `R-READ-001`. A rule that cannot run is "not evaluated", never a pass. |
| `rules/models.py` | `Rule`, `Finding`, `Evidence`, severities (a workflow priority, not a regulatory class). |
| `pipeline.py`, `verification.py` | `analyze_pair` (text and fields) and `verify_pair` (plus barcodes, artwork and rules), the single entry point used by the UI. |

## Change impact (pure)
| Module | Purpose |
|---|---|
| `impact/models.py` | Catalog records (products, configurations, labels, markets, languages, packaging levels, relationships, review records) and impact results. |
| `impact/rules.py` | Strict loader for `config/impact_rules.toml`. |
| `impact/engine.py` | From a changed parameter to the label records that may need review, through explicit relationships and configured rules; also reports which labels were not flagged and why. |
| `impact/changes.py` | Turns flagged findings into parameter changes and suggests the matching catalog configuration. |
| `impact/demo_data.py` | A small synthetic catalog (4 products, 14 labels) for demonstrations. |

## Optional AI (no database access)
| Module | Purpose |
|---|---|
| `ai/config.py` | Settings from environment variables only; the key is excluded from `repr`. |
| `ai/prompt.py` | Builds the request from findings, rule descriptions and evidence quotes; label text goes in JSON data fields. |
| `ai/provider.py` | Claude adapter (SDK imported lazily); maps every failure to a fixed, safe message. |
| `ai/validate.py` | Strict validation of the model's answer; any violation rejects the whole answer. |
| `ai/fallback.py`, `ai/service.py` | The deterministic explanation, and the service that uses AI only when configured, confirmed and trustworthy. |

## Storage and reporting
| Module | Purpose |
|---|---|
| `db/schema.py` | Versioned migrations (`PRAGMA user_version`); upgrades Day 1 and Day 2 databases in place. |
| `db/database.py` | Analysis jobs, extracted fields, findings, review decisions, review history and the review queue. |
| `db/audit.py` | Append-only audit events, written in the same transaction as the change. |
| `db/catalog.py`, `db/explanations.py` | Catalog, demo data load and clear, impact results; stored explanations. |
| `workflow.py` | Thin orchestration used by the pages: impact run, explanation, report-generated events. |
| `reporting/export.py` | Markdown reports: comparison, verification (with review decisions), impact, explanation. |
| `evaluation.py` | The synthetic evaluation corpus, precision / recall / F1, robustness cases, timing, and the report writer. |
| `samples/synthetic_labels.py` | Fictional labels (approved, revised, incomplete, scanned) rendered with ReportLab. |

## User interface (presentation only)
| Module | Purpose |
|---|---|
| `ui/state.py` | Session state helpers and the review action. |
| `ui/upload.py`, `ui/dashboard.py`, `ui/findings.py`, `ui/visual.py`, `ui/results.py`, `ui/rules_page.py` | The Day 1 and Day 2 pages. |
| `ui/ai_page.py`, `ui/queue_page.py`, `ui/impact_page.py`, `ui/history_page.py`, `ui/audit_page.py` | The Day 3 pages. |
| `ui/review_widget.py`, `ui/report_widget.py`, `ui/finding_view.py`, `ui/components.py`, `ui/style.py` | Shared pieces: review controls, guarded report download, finding display, styling. |
