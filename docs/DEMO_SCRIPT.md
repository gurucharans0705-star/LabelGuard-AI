# Demonstration script (about 8 minutes)

Uses only the built-in fictional labels. Say at the start: *"LabelGuard flags differences for a person to review. It does not
decide that a label is compliant."*

**Before you start:** `streamlit run app.py` (an empty database is fine). Optional: set `ANTHROPIC_API_KEY` to show the AI
mode; the demo works the same without it.

1. **Dashboard (30 s).** Point at the status tiles and the disclaimer in the sidebar.
2. **Upload & Analyze (1 min).** Expand *No labels at hand?*, press **Load selected samples** (Approved v1, Revised v2), then
   **Start label analysis**. Mention validation: wrong type, empty, encrypted, oversized and too-long files are refused first.
3. **Findings (2 min).** 8 discrepancies, ranked. Open the REF finding: both values, the evidence quote with page number, the
   recommended action. Show *Safety warnings: statement missing* (the removed "Do not use if package is damaged"). Try a
   filter, then **Reset filters**.
4. **Visual Comparison (30 s).** Changed areas and decoded barcodes. Say what is *not verified* is never shown as a pass.
5. **Review (1 min).** Type a name in **Reviewer**. Press **Start review** on one finding; try **Resolve** without a rationale
   (it is refused), add one, resolve it. Open **Review Queue**: the default list shows only work still to do, so the resolved
   finding is not in it, and the page says how many are hidden (not deleted). Add **Resolved** to the *Status* filter to list it.
6. **Audit History (30 s).** The analysis, both status changes with who and when. Say: the log is append-only in the
   application, and is a safeguard, not tamper-proofing.
7. **Change Impact (1 min).** *Demonstration data -> Load synthetic demo catalog.* Back on **Change Impact**, press
   **Analyze impact of this change**: the kit label that contains the infusion set is flagged with the reason and action; the
   unrelated syringe and catheter labels are not. Say: synthetic catalog, no PLM/ERP connection.
8. **AI Explanations (1 min).** Generate the deterministic explanation. With a key: choose AI-assisted, tick the confirmation,
   generate; show the "AI-assisted" label and the evidence links. Say what happens if the model answers badly: the whole
   answer is rejected and the deterministic text is shown with the reason.
9. **Report (30 s).** On Findings, **Download verification report (Markdown)**; open **Audit History**: the download is logged.

**If asked:** accuracy figures are in `docs/EVALUATION_REPORT.md` and describe a small synthetic corpus only; limits are in the
README.
