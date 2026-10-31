# LabelGuard AI - architecture

LabelGuard AI is one Python application (Streamlit user interface, SQLite file, no separate server). The comparison
and rule logic never imports Streamlit, so it can be tested without a browser. The AI layer is optional and sits *beside*
the deterministic core: it can restate findings but has no way to change them.

## System architecture

```mermaid
flowchart TB
    user([Reviewer])

    subgraph ui["Streamlit user interface (labelguard/ui, app.py)"]
        direction LR
        nav["app.py: navigation and page guard"]
        pages["Pages: Dashboard, Upload and Analyze, Findings, Visual Comparison, Results, AI Explanations, Review Queue, Change Impact, Product History, Audit History, Rules"]
        shared["Shared widgets: review controls, report download, finding view"]
    end

    subgraph core["Deterministic core (no AI, no network)"]
        direction TB
        ingest["ingestion.py: validate upload, private temp folder, OCR fallback"]
        extract["extraction/: PyMuPDF text, Tesseract OCR, page images"]
        fields["fields/: label fields with evidence"]
        compare["comparison/: field and text differences"]
        vision["vision/: artwork diff (OpenCV), barcodes (zxing-cpp)"]
        rules["rules/: config/rules.toml to findings with evidence"]
        verify["verification.py: verify_pair"]
        impact["impact/: config/impact_rules.toml, synthetic catalog"]
    end

    subgraph ai["Optional AI layer (labelguard/ai)"]
        direction TB
        prompt["prompt.py: findings, rule text, evidence quotes only"]
        provider["provider.py: Claude adapter, key from environment"]
        validate["validate.py: strict response validation"]
        fallback["fallback.py: deterministic explanation"]
    end

    subgraph store["SQLite (labelguard/db)"]
        direction TB
        jobs[("jobs, fields, findings")]
        reviews[("reviews and review history")]
        audit[("audit events: append-only")]
        catalog[("catalog, impact results, explanations")]
    end

    workflow["workflow.py: orchestration, no logic of its own"]
    report["reporting/export.py: Markdown reports"]
    claude{{"Claude API (only if a key is set and the person confirms)"}}

    user --> nav --> pages
    pages --> shared
    pages --> verify
    pages --> workflow
    pages --> report
    verify --> ingest --> extract
    verify --> fields --> compare --> rules
    verify --> vision --> rules
    workflow --> impact
    workflow --> prompt
    prompt --> provider --> claude
    provider --> validate
    validate -->|accepted| workflow
    validate -->|rejected, any failure| fallback --> workflow
    workflow --> store
    pages -->|"db/ functions"| store
```

## Data flow for one comparison

```mermaid
flowchart LR
    a["Approved PDF"] --> v["Validate: type, size, pages, encryption"]
    b["Revised PDF"] --> v
    v -->|refused| err["Clear message, nothing analysed"]
    v --> t["Text from PDF, OCR for scanned pages"]
    t --> f["Fields with evidence"]
    f --> c["Compare fields and text"]
    t --> img["Render pages: artwork diff and barcodes"]
    c --> r["Rules to findings: flagged or not evaluated"]
    img --> r
    r --> save[("Save job, fields, findings, audit event")]
    r --> review["Person reviews: start, escalate, resolve, reopen"]
    review --> hist[("Decision, rationale, reviewer, time; history and audit kept")]
    r --> imp["Change-impact analysis against the synthetic catalog"]
    r --> exp["Explanation: deterministic, or AI-assisted after validation"]
    r --> rep["Markdown report, download is audited"]
```

## Design decisions that matter

- **Findings are the source of truth.** A finding has a stable id, a rule id, evidence quotes with page numbers, and a
  recommended reviewer action. A check that could not run is *not evaluated*, never a pass.
- **Severity is a workflow priority**, not a regulatory classification.
- **The AI cannot decide.** It receives finding ids, rule descriptions and evidence quotes (not PDFs), and its answer is shown
  only if it passes validation (known finding ids, cited evidence, no invented values, numbers, quotes or regulation
  citations, no compliance, approval, release or safety claims). Label text is passed as JSON data fields and the system prompt
  says it is untrusted. The `ai` package does not import the database and has no review or status functions.
- **Reviews are people's decisions.** A named reviewer is required, Resolve and Escalate need a rationale, there is no
  "approved" or "released" status, and nothing is deleted: every change is in the review history and the audit log.
- **The audit log is append-only** (SQLite triggers refuse UPDATE and DELETE) and each event is written in the same transaction as
  the change it records. This guards against mistakes; it is not tamper-proofing.
- **Uploads are validated before anything is read**, processed from a private temporary folder that is always removed, and
  never stored. The database holds extracted field values and short evidence quotes, not PDFs.
- **Integration points** with real PLM/ERP data are described in `INTEGRATION_POINTS.md`; none exist today.
