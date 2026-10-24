"""Rules page: the configured validation rules and how each performed in the latest analysis."""
from __future__ import annotations

import pandas as pd
import streamlit as st

from labelguard import config
from labelguard.rules.loader import RuleConfigError, load_rules
from labelguard.rules.models import CATEGORIES, SEVERITIES, SEVERITY_LABELS, SEVERITY_NOTE
from labelguard.ui import state
from labelguard.ui.style import page_header

_OUTCOME_LABELS = {"passed": "Passed", "flagged": "Flagged", "not_evaluated": "Not evaluated", "skipped": "Skipped"}


def render_rules_page() -> None:
    page_header("Rules", "The checks that produce findings. They are defined in a readable file you can edit")
    st.markdown(
        f"Rules are read from `{config.RULES_PATH}`. Open that file in any text editor, change a rule's severity, "
        "turn it off (`enabled = false`), add a rule or edit its recommended action, save it, and start a new "
        "analysis. A mistake in the file is reported here with the rule id and the problem."
    )
    st.caption(SEVERITY_NOTE)
    st.button("Reload rules from file", key="reload_rules")

    try:
        rules = load_rules()
    except RuleConfigError as exc:
        st.error(str(exc))
        st.caption("Fix the file and press **Reload rules from file**. No analysis can run until the rules file is valid.")
        return

    result = state.get_verification()
    outcomes = {o.rule_id: o for o in result.report.outcomes} if result else {}
    enabled = sum(rule.enabled for rule in rules)
    st.write(f"**{len(rules)}** rules, **{enabled}** enabled.")

    c1, c2 = st.columns(2)
    severities = c1.multiselect(
        "Severity", SEVERITIES, default=list(SEVERITIES), format_func=SEVERITY_LABELS.get, key="rules_severity"
    )
    show = c2.radio("Show", ["All rules", "Enabled only", "Disabled only"], horizontal=True, key="rules_show")

    rows = []
    for rule in rules:
        if rule.severity not in severities:
            continue
        if (show == "Enabled only" and not rule.enabled) or (show == "Disabled only" and rule.enabled):
            continue
        outcome = outcomes.get(rule.id)
        rows.append({
            "Rule": rule.id,
            "Name": rule.name,
            "Severity": SEVERITY_LABELS[rule.severity],
            "Category": CATEGORIES.get(rule.category or "", "Depends on the change"),
            "Enabled": "Yes" if rule.enabled else "No",
            "Condition": rule.condition,
            "Recommended reviewer action": rule.action,
            "Last analysis": _OUTCOME_LABELS.get(outcome.outcome, outcome.outcome) if outcome else "—",
            "Note": outcome.note if outcome else "",
        })
    if not rows:
        st.info("No rule matches the current filters.")
        return
    st.dataframe(pd.DataFrame(rows), hide_index=True, width="stretch")

    with st.expander("Rule types you can use in the file"):
        st.markdown(
            "- `required_field`: a field must be detected\n"
            "- `field_not_empty`: a keyword such as LOT: must not be printed without a value\n"
            "- `field_change`: a field's value changed between the two labels\n"
            "- `text_change`: text differences not already explained by a field finding\n"
            "- `page_count_changed`, `date_order`, `udi_check_digit`\n"
            "- `barcode_text_match`, `barcode_changed`, `visual_difference`\n\n"
            "The comments at the top of the rules file explain every setting."
        )
