# LabelGuard AI

Medical device label verification and change-impact review (MVP in development, Day 3 of 4).

Upload an **approved** label and a **revised** label as PDFs. LabelGuard AI extracts the text (using OCR
for scanned pages), pulls out key fields with the label text as evidence, and shows what was added,
removed or modified. Day 2 added a configurable rules engine that turns differences into ranked findings with
evidence, a visual (artwork) comparison and barcode decoding. Day 3 connects this to a **synthetic product catalog**
(change-impact analysis), adds an **audit history**, a **review workflow** with named reviewers and rationales, and
**optional AI-assisted explanations** that fall back to a deterministic explanation when no API key is set.

> **Important:** LabelGuard AI is a decision-support tool. Its findings are evidence-based flags for
> human review. They do not establish or certify regulatory compliance. All sample labels are fictional.

## Architecture

A single Python application (no separate backend, no Docker). Business logic never imports Streamlit,
so it can be tested on its own.

```
app.py                         Streamlit entry point: sidebar navigation, page routing
config/rules.toml              The validation rules (readable, editable)
labelguard/
  config.py                    Paths, limits, OCR and database settings (environment variables)
  ingestion.py                 Validate upload -> extract (temp file, always deleted) -> OCR fallback -> notes
  pipeline.py                  analyze_pair(): text + field comparison (Day 1)
  verification.py              verify_pair(): analyze_pair + barcodes + visual + rules -> VerificationResult
  evaluation.py                Synthetic corpus with expected findings; precision / recall (python -m labelguard.evaluation)
  rules/                       models (Finding, Evidence, Rule), loader (strict TOML validation), engine (evaluators)
  vision/                      images (OpenCV helpers), compare (page pairing, alignment, diff), barcode (zxing-cpp)
  extraction/
    pdf_text.py                PyMuPDF text, lines with page + position, page/size/encryption checks
    ocr.py                     Tesseract OCR (pytesseract + Pillow); reports problems, never crashes
    normalize.py               Text clean-up that keeps meaningful symbols (°, µ, ±, ...)
    pdf_render.py              Page -> PNG
  fields/
    extractor.py               Rule-based fields, each with evidence text, page and "how found"
    language.py                Small stop-word language detector (says "not detected" when unsure)
    models.py                  Field data classes
  comparison/
    engine.py                  Added / removed / modified comparison (fields; lines or words; moved lines)
    models.py                  Result data classes (neutral wording, no "violation")
  validation/checks.py         Completeness observations ("not detected - please check")
  validation/identifiers.py    GTIN check digit, UDI extraction, label date parsing
  db/schema.py                 SQLite migrations (PRAGMA user_version); upgrades Day 1/2 databases in place
  db/database.py               Analysis jobs, extracted fields, findings, reviews, review queue (never label PDFs)
  db/audit.py                  Append-only audit log (written in the same transaction as the change)
  db/catalog.py                Product catalog, demo data load/clear, saved impact results
  db/explanations.py           Stored explanations (text + evidence references only)
  impact/                      Change-impact engine, rule loader, demo catalog (no Streamlit, no database code)
  ai/                          Provider adapter, prompt, strict response validation, deterministic fallback
  workflow.py                  Thin orchestration: impact run, explanation, report-generated events
  reporting/export.py          Markdown comparison and verification reports
  samples/synthetic_labels.py  Fictional labels (approved, revised, incomplete, scanned)
  ui/                          Streamlit pages (11, see below) and shared review controls
config/impact_rules.toml       Change-impact rules (readable, editable)
docs/INTEGRATION_POINTS.md     Where real PLM/ERP data would plug in (nothing is integrated today)
tests/                         pytest suite (328 tests)
data/samples/                  Generated sample PDFs
```

Data flow: **PDF -> validation -> text (digital or OCR) -> fields + evidence -> comparison -> barcodes and
visual diff -> rules -> findings (evidence, severity, action) -> review decisions, reports, run log.**

Severity (Critical review / High / Medium / Low / Informational) is a **workflow priority**, not a regulatory
classification. A check that could not run (no OCR, no barcode decoder, a scan) is reported as **not verified**;
it is never counted as a pass. If a label has no readable text (an image-only scan where OCR is unavailable
or off), text rules are **not evaluated** for it (rule R-READ-001, built into the engine) instead of reporting
every field as missing. The rules and the impact analysis are deterministic; the AI layer only restates their results.

## Setup on Windows (PowerShell)

1. Install **Python 3.11 or newer** from python.org (tick "Add python.exe to PATH").
2. *(Optional, only for scanned PDFs)* Install **Tesseract OCR** (the "UB Mannheim" Windows installer).
   Keep the default install folder; the app finds it automatically. If you install it elsewhere, set
   `TESSERACT_CMD` to the full path of `tesseract.exe`. Without Tesseract everything else still works and
   scanned pages are flagged clearly.
3. In PowerShell, inside the project folder:

```powershell
py -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -r requirements.txt
```

If activation is blocked ("running scripts is disabled"), run this once in the same window and activate again:

```powershell
Set-ExecutionPolicy -Scope Process -ExecutionPolicy Bypass
```

## If the app will not start

`ImportError: cannot import name '__version__' from 'labelguard'` means Python found a `labelguard` folder with
no usable `labelguard\__init__.py` (often a partial copy or extraction). Check with:

```powershell
python -c "import labelguard; print(labelguard.__file__)"   # must print a path, not None
```

If it prints `None`, restore `labelguard\__init__.py` and `labelguard\version.py` from the project zip, and delete
any `__pycache__` folders.

## Run

```powershell
streamlit run app.py            # opens http://localhost:8501
python -m pytest                # runs all tests
python -m labelguard.evaluation # precision / recall on the synthetic corpus
python -m labelguard.samples.synthetic_labels   # (re)creates the sample PDFs in data\samples
```

Open a new terminal later? Activate the environment again (`.venv\Scripts\Activate.ps1`) first.

## Pages

Dashboard · Upload & Analyze · Findings (finding details and review) · Visual Comparison · Results ·
**AI Explanations** · **Review Queue** · **Change Impact** · **Product History** · **Audit History** · Rules.
Review controls and all business logic are shared: pages call the same functions (`db/`, `impact/`, `ai/`, `workflow.py`).

## Try it

1. Open **Upload & Analyze**.
2. Upload your own two PDFs, or expand "No labels at hand?" and press **Load selected samples**.
3. Press **Start label analysis**. The **Findings** page opens: summary cards, filters, a findings table,
   expandable details (original vs revised, evidence, pages, recommended action) and review controls.
4. **Visual Comparison** shows old and new pages side by side with changed areas marked, plus decoded barcodes.
   **Results** keeps the Day 1 field and text comparison. **Rules** lists every rule.
5. To see OCR, load *Scanned copy of approved label (image only)* as the revised label.
6. **Change Impact -> Demonstration data -> Load synthetic demo catalog**, analyze the sample pair, then open
   **Change Impact** and press **Analyze impact of this change** to see which catalog label records may need review,
   with markets, languages, the reason and the recommended action for each. A *What-if change* tab tests any parameter.
7. Enter your name in **Reviewer** on the Findings page, then Start review / Escalate / Resolve / Reopen.
   Resolve and Escalate need a short rationale. **Review Queue** lists every finding across analyses;
   **Audit History** shows each event (it is never edited or deleted by the app).

## Review workflow

Statuses: **Pending review -> Under review -> Escalated / Resolved** (and Reopen back to Pending). Every change records
the reviewer, a UTC timestamp and a rationale. A named person is required: names such as "AI" or "Claude" are
refused, and there is no "approved" or "released" status. A resolved finding stays in the queue, the review history
and the audit log. A status is a person's decision about one finding for one pair of files; it does not change the
finding and it is not a label approval or release.

## Optional AI explanations

The app works fully without any key. To enable AI-assisted explanations, set environment variables **before**
starting the app (the app does **not** read a `.env` file, and no key is ever written to code, logs, the database or reports):

```powershell
$env:ANTHROPIC_API_KEY = "paste-your-key-here"      # this PowerShell window only
pip install anthropic                                # only needed once, for the optional AI layer
streamlit run app.py
```

Optional: `LABELGUARD_AI_MODEL` (default `claude-sonnet-5-5`), `LABELGUARD_AI_TIMEOUT_S` (30),
`LABELGUARD_AI_MAX_TOKENS` (1500), `LABELGUARD_AI_ENABLED=false` to switch AI off even when a key exists.
On the **AI Explanations** page choose *AI-assisted* (shown only when a key is configured; you must confirm that the
extracted evidence quotes will be sent to the API). The model receives finding ids, rule descriptions and evidence
quotes only - never the PDFs. Its answer is shown only if it passes strict validation (known finding ids, cited
evidence that belongs to the finding, no invented values, numbers, quotes or regulation citations, no compliance,
approval, release or safety claims). Anything else - no key, timeout, API error, malformed or ungrounded answer -
falls back to the deterministic explanation, with a note saying why. AI text is always labelled **AI-assisted**.

## Database

SQLite file (`data/labelguard.db`, or `LABELGUARD_DB_PATH`). It stores identifiers, document names and SHA-256 hashes,
extracted field values, finding text and short evidence quotes, reviewer names, rationales and timestamps. It does
**not** store PDFs, page images or full label text. Treat the file as confidential if you analyse real labels.
Tables: `analysis_runs` (jobs), `extracted_fields`, `analysis_findings`, `finding_reviews`, `review_events`,
`audit_events` (append-only), `products`, `product_configurations`, `config_parameters`, `product_relationships`,
`label_versions`, `label_required_fields`, `label_review_records`, `markets`, `languages`, `packaging_levels`,
`impact_runs`, `impact_items`, `ai_explanations`. Synthetic rows carry `is_demo = 1`; **Change Impact -> Demonstration
data -> Clear** removes them (the audit log keeps its history and records the clearing).

## Editing the rules

Open `config\rules.toml` in a text editor. Each `[[rule]]` has an id, name, description, type, severity,
category, a recommended reviewer action and `enabled`. Change a severity, set `enabled = false`, or add a rule,
save, and run a new analysis (the Rules page has a reload button). A mistake (unknown severity, misspelled
setting, duplicate id) is reported with the rule id; no rule is silently ignored.

## Evaluation

`python -m labelguard.evaluation` runs 21 synthetic label pairs with known expected findings (changed REF,
UDI, warning removed, reordered text, extra page, ...) and prints precision and recall per case. The corpus is
fictional and generated by the same code that renders the labels, so a perfect score only shows that nothing
regressed. It is **not** an estimate of accuracy on real labels.

## Settings (all optional)

See `.env.example` (a list of the variables - it is not loaded automatically): `TESSERACT_CMD`,
`LABELGUARD_OCR_ENABLED`, `LABELGUARD_OCR_LANG`, `LABELGUARD_MAX_UPLOAD_MB` (default 25), `LABELGUARD_MAX_PAGES`
(default 20), `LABELGUARD_DB_PATH`, `LABELGUARD_RULES_PATH`, `LABELGUARD_IMPACT_RULES_PATH`, and the AI variables above.

## Known limitations

- Fields are found with rules and patterns; unusual wording can be missed. A missing field is reported as
  "Not detected", never guessed. Rules only compare the two labels and check what is configured; passing
  them says nothing about compliance.
- OCR can misread characters. When a scan is involved, text is compared word by word, and the artwork is
  **not** compared by pixels (it is reported as not verified).
- Visual comparison is a basic render-and-diff: it handles small shifts only, cannot compare pages with
  different proportions, and a difference may be intentional. Text-change areas are marked as explained.
- A barcode counts as decoded only if zxing-cpp returned a value. Print quality is not assessed.
- Dates like 04/05/2029 are ambiguous and are not interpreted. Moved or reworded warnings are matched by
  text similarity and may be reported as removed + added.
- The synthetic corpus measures regression only. No real labels were used.
- The product catalog is synthetic and local. LabelGuard is **not** connected to any PLM, ERP, SAP, FDA or EU system
  (see `docs/INTEGRATION_POINTS.md`). Impact rules are organisation workflow rules in `config/impact_rules.toml`, not
  regulatory requirements; "no label flagged" does not mean "no label affected".
- Reviewer names are typed, not authenticated, and the audit log's append-only triggers guard against mistakes only;
  someone with direct access to the database file could change it. There are no user accounts.
- AI validation is deliberately strict and may reject acceptable answers (the deterministic text is shown then).
  It checks grounding and forbidden claims by pattern; it cannot prove that an explanation is correct.
  AI behaviour was tested with mocked responses only; no live API call was made during development.
- Not built yet: PDF reports, user accounts and authentication.
