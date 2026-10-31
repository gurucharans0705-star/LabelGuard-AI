# Future enhancements

Not built. Ordered roughly by value for a first real-world pilot.

1. **Real-document evaluation.** Evaluate on real, permitted labels with ground truth set by an independent reviewer; report
   precision and recall with confidence intervals. Today's numbers are from a small synthetic corpus written by the same author.
2. **User accounts and authentication.** Reviewer names are typed, not verified. Add sign-in, roles (reviewer, approver, auditor)
   and per-user audit entries.
3. **Tamper-evident audit trail.** Hash-chained events or export to write-once storage, instead of relying on database triggers.
4. **PDF report export** (a printable report with the findings, evidence images and decisions); today reports are Markdown.
5. **Real catalog connectors.** Replace the synthetic catalog with a read-only import from the organisation's PLM/ERP export;
   `docs/INTEGRATION_POINTS.md` lists the mapping.
6. **Better extraction.** Layout-aware field extraction for multi-column labels, multilingual labels, symbols (pictograms) and
   tables; field detection beyond the current rule patterns.
7. **Stronger artwork comparison.** Region-based comparison that tolerates scaling and different page proportions, and a
   scan-versus-digital comparison.
8. **OCR quality.** Other OCR languages, image preprocessing, and a confidence threshold that is tuned on real scans.
9. **Larger-scale operation.** A background job queue for big documents or batches, a stated performance budget, and a
   production database (PostgreSQL) for several users.
10. **AI evaluation.** Measure how often the validator rejects acceptable answers and how often it accepts a flawed one, with a
    live model and a reviewed sample; consider a second provider through the existing adapter.
11. **Packaging.** A Windows installer or Docker image, signed releases, dependency pinning and vulnerability scanning.
12. **Accessibility and localisation** of the interface.
