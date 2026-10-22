# LabelGuard AI

Medical device label verification and change-impact review (MVP in development, Day 1 of 4 complete).

Upload an **approved** label and a **revised** label as PDFs. LabelGuard AI extracts the text (using OCR
for scanned pages), pulls out key fields with the label text as evidence, and shows what was added,
removed or modified.

> **Important:** LabelGuard AI is a decision-support tool. Its findings are evidence-based flags for
> human review. They do not establish or certify regulatory compliance. All sample labels are fictional.

## Architecture

A single Python application (no separate backend, no Docker). Business logic never imports Streamlit,
so it can be tested on its own.

```
app.py                         Streamlit entry point: sidebar navigation, page routing
labelguard/
  config.py                    Paths, limits, OCR and database settings (environment variables)
  ingestion.py                 Validate upload -> extract (temp file, always deleted) -> OCR fallback -> notes
  pipeline.py                  analyze_pair(): the one function the UI calls
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
    engine.py                  Added / removed / modified / exact-match comparison (fields and lines)
    models.py                  Result data classes (neutral wording, no "violation")
  validation/checks.py         Completeness observations ("not detected - please check")
  db/database.py               SQLite run log (file names, SHA-256, counts; never label text)
  reporting/export.py          Markdown comparison report and table rows
  samples/synthetic_labels.py  Fictional labels (approved, revised, incomplete, scanned)
  ui/                          Streamlit pages: dashboard, upload, results, styling, session state
tests/                         pytest suite (117 tests)
data/samples/                  Generated sample PDFs
```

Data flow: **PDF -> validation -> text (digital or OCR) -> fields + evidence -> comparison -> results,
report download, run log.**

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

## Run

```powershell
streamlit run app.py            # opens http://localhost:8501
python -m pytest                # runs all tests
python -m labelguard.samples.synthetic_labels   # (re)creates the sample PDFs in data\samples
```

Open a new terminal later? Activate the environment again (`.venv\Scripts\Activate.ps1`) first.

## Try it

1. Open **Upload & Analyze**.
2. Upload your own two PDFs, or expand "No labels at hand?" and press **Load selected samples**.
3. Press **Start label analysis**. The **Results** page opens with field differences, text differences,
   extracted fields with evidence, extracted text and a downloadable Markdown report.
4. To see OCR, load *Scanned copy of approved label (image only)* as the revised label.

## Settings (all optional)

See `.env.example`: `TESSERACT_CMD`, `LABELGUARD_OCR_ENABLED`, `LABELGUARD_OCR_LANG`,
`LABELGUARD_MAX_UPLOAD_MB` (default 25), `LABELGUARD_MAX_PAGES` (default 20), `LABELGUARD_DB_PATH`.
No API keys are used or stored.

## Known limitations (Day 1)

- Fields are found with rules and patterns; unusual wording can be missed. A missing field is reported as
  "Not detected", never guessed. The product name is inferred from the largest text line when no
  "Product:" keyword exists, and is marked as inferred.
- OCR can misread characters and can split lines differently from the original, so line-level text
  differences between a scan and a digital PDF may reflect layout. Compare fields first.
- Comparison ignores case, repeated spaces and dash style.
- Not built yet: artwork (image) comparison, configurable validation rules, per-finding review history,
  PDF reports, optional AI explanations.
