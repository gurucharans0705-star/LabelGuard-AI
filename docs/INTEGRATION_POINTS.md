# Integration points

LabelGuard AI is a local application. **It is not integrated with any PLM, ERP, SAP, FDA or EU regulatory
system, and nothing in this repository claims otherwise.** Every product, label record, market, language and
review record it shows in the Change Impact and Product History pages is synthetic demonstration data, created by
`labelguard/impact/demo_data.py` and stored in the local SQLite file. This document says where a real source would
connect, so the work can be planned without rewriting the engine.

## 1. Product catalog (PLM / ERP / master data)

**Today:** `labelguard/impact/demo_data.py` builds a `Catalog` (products, configurations and their parameters, label
records with market / language / packaging level / the fields each label shows, product relationships, review
records). `labelguard/db/catalog.py` saves it to SQLite and loads it back.

**Integration point:** anything that can produce a `labelguard.impact.models.Catalog` can be analysed. A connector
would implement `load() -> Catalog` against the source system's export or API and call `catalog.save_catalog(...,
is_demo=False)`. The impact engine (`labelguard/impact/engine.py`) reads only the `Catalog` object; it has no
database or network code.

Mapping needed by a real source:

| LabelGuard record | Typically comes from |
| --- | --- |
| `Product`, `ProductConfiguration.parameters` | product master data / item master |
| `ProductRelationship` (`contains_component`) | bill of materials |
| `LabelVersion` (+ market, language, packaging level, status) | label / artwork management system |
| `LabelVersion.required_fields` | the label template or specification |
| `LabelReviewRecord` | the document or review system |

Parameter names are the label field keys in `labelguard/fields/models.py` (`product_identifier`, `udi`,
`safety_warnings`, ...). A source with other names needs a mapping table at the connector.

## 2. Change events (change control)

**Today:** a change comes from the fields that differ between an approved and a revised label (flagged findings, via
`labelguard/impact/changes.py`), or from a what-if entered on the Change Impact page.

**Integration point:** a change-control system could submit `ParameterChange(config_id, parameter, old_value,
new_value)` objects to `labelguard.workflow.run_impact_analysis(...)`. The result is stored in `impact_runs` and
`impact_items` and logged in the audit trail.

## 3. Impact rules

`config/impact_rules.toml` states which parameter change makes which labels worth reviewing (by configuration,
whole product, or products that contain the changed product). These are **organisation-specific workflow rules, not
regulatory requirements**: LabelGuard does not interpret any regulation. A company would set them from its own
procedures.

## 4. Reviews and the audit trail

Review statuses and the audit log (`audit_events`) are local. A real deployment might forward audit events to a
quality system. `labelguard/db/audit.py` is the single place events are written, so an exporter or a webhook would
attach there. The log is append-only through this application (database triggers refuse UPDATE and DELETE); that is
a safeguard against mistakes, not tamper-proofing. A regulated deployment would need access control, electronic
signatures and validated storage, none of which exist here.

## 5. AI explanations

`labelguard/ai/provider.py` defines a small `Provider` protocol (`complete(system, user) -> str`). `ClaudeProvider`
is the only implementation. It runs only when `ANTHROPIC_API_KEY` is set and a person chooses AI-assisted mode; it
sends finding text and evidence quotes (never the PDFs) to the Claude API. Another provider, including one that runs
inside the company network, can implement the same protocol. Validation (`labelguard/ai/validate.py`) applies to any
provider, and a deterministic explanation is used whenever a provider is missing or its answer is rejected.

## 6. What is deliberately not connected

* No regulatory database lookups, no submission or registration systems.
* No automatic approval, release, or status change by software: every status change needs a named person.
* No data leaves the computer unless an API key is configured and AI-assisted mode is chosen and confirmed.
