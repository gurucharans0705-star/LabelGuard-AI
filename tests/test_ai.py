"""Optional Claude explanations: grounding, validation, fallback, errors, secrets (Day 3). No network, no real key."""
import json
import logging
import re
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from labelguard.ai import prompt, service, validate
from labelguard.ai.config import AiSettings, get_settings
from labelguard.ai.models import (
    AI_ASSISTED, DETERMINISTIC, AiRequestError, AiResponseError, AiTimeout, AiUnavailable,
)
from labelguard.ai.provider import ClaudeProvider
from labelguard.db import audit, database
from labelguard.rules.loader import load_rules

SENTINEL_KEY = "TEST-SENTINEL-not-a-real-key-0000"
SETTINGS = AiSettings(api_key=SENTINEL_KEY, model="test-model", timeout_s=5)


@pytest.fixture(scope="module")
def findings(sample_verification):
    return tuple(sample_verification.report.flagged[:3])


@pytest.fixture(scope="module")
def rules():
    return load_rules()


@pytest.fixture
def inputs(findings, rules):
    return prompt.build_inputs(findings, rules)[0]


class FakeProvider:
    name, model = "fake", "fake-model"

    def __init__(self, reply=None, error=None):
        self.reply, self.error, self.calls = reply, error, []

    def complete(self, system, user):
        self.calls.append((system, user))
        if self.error:
            raise self.error
        return self.reply


def good(inputs, **over):
    """A well-formed, grounded answer built from the real input."""
    first = inputs[0]
    body = {
        "summary": "The rules flagged differences between the two labels. A reviewer should look at each one.",
        "findings": [{"finding_id": f.finding_id, "explanation": f"The configured rule flagged this field because the two labels differ.",
                      "evidence_refs": [f.refs[0]] if f.refs else []} for f in inputs],
        "reviewer_questions": ["Is each change covered by an approved change request?"],
    }
    body.update(over)
    return json.dumps(body)


def explain(findings, rules, reply=None, error=None, mode="ai"):
    provider = FakeProvider(reply, error)
    return service.explain(findings, rules, mode=mode, provider=provider, settings=SETTINGS), provider


# --- settings and the missing key -----------------------------------------------------------------------------
def test_no_key_means_not_configured_and_the_app_still_explains(monkeypatch, findings, rules):
    monkeypatch.delenv("ANTHROPIC_API_KEY", raising=False)
    settings = get_settings()
    assert not settings.configured and "ANTHROPIC_API_KEY" in settings.status_message
    result = service.explain(findings, rules, mode="ai", settings=settings)  # asks for AI, has no key
    assert result.source == DETERMINISTIC and "unavailable" in result.note.lower() and result.items


def test_settings_come_from_the_environment_and_hide_the_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)
    monkeypatch.setenv("LABELGUARD_AI_MODEL", "m-1")
    monkeypatch.setenv("LABELGUARD_AI_TIMEOUT_S", "7")
    monkeypatch.setenv("LABELGUARD_AI_MAX_TOKENS", "oops")  # a bad number falls back to the default
    s = get_settings()
    assert s.configured and s.model == "m-1" and s.timeout_s == 7 and s.max_tokens == 1500
    assert SENTINEL_KEY not in repr(s) and SENTINEL_KEY not in str(s)


def test_the_switch_turns_ai_off_even_with_a_key(monkeypatch):
    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)
    monkeypatch.setenv("LABELGUARD_AI_ENABLED", "false")
    assert not get_settings().configured


def test_deterministic_mode_never_calls_the_provider(findings, rules):
    result, provider = explain(findings, rules, mode="deterministic")
    assert provider.calls == [] and result.source == DETERMINISTIC and result.label == "Deterministic (no AI)"


# --- the deterministic fallback ---------------------------------------------------------------------------------
def test_fallback_restates_every_finding_with_its_evidence_and_claims_nothing(findings, rules):
    result = service.explain(findings, rules)
    assert [i.finding_id for i in result.items] == [f.id for f in findings] == list(result.finding_ids)
    assert all(i.generated_by == DETERMINISTIC and i.text for i in result.items)
    assert all(i.evidence_refs for i in result.items)
    assert "not a compliance determination" in result.summary and result.questions
    text = " ".join([result.summary, *(i.text for i in result.items)]).lower()
    assert not re.search(r"\b(is|are) (compliant|safe|approved)\b|ready for release", text)


def test_fallback_for_no_findings_does_not_say_the_labels_are_fine(rules):
    result = service.explain((), rules, mode="ai", provider=FakeProvider(), settings=SETTINGS)
    assert "not a statement that either label is acceptable" in result.summary


# --- a good answer ------------------------------------------------------------------------------------------------
def test_a_valid_answer_is_labelled_ai_assisted_and_keeps_evidence_links(findings, rules, inputs):
    result, provider = explain(findings, rules, good(inputs))
    assert result.source == AI_ASSISTED and result.label == "AI-assisted" and result.model == "fake-model" and not result.note
    assert all(i.generated_by == AI_ASSISTED for i in result.items)
    for item in result.items:  # every cited reference resolves back to the original evidence
        assert item.evidence_refs
        for ref in item.evidence_refs:
            assert prompt.resolve_ref(findings, ref) is not None
    system, user = provider.calls[0]
    for f in findings:  # the model was given the finding ids, rule descriptions and evidence
        assert f.id in user
    assert "rule_description" in user and "quote" in user and SENTINEL_KEY not in system + user


def test_the_request_contains_no_pdf_data_or_full_label_text(findings, rules, sample_verification):
    inputs, _ = prompt.build_inputs(findings, rules)
    message = prompt.user_message(inputs)
    assert "%PDF" not in message
    assert sample_verification.analysis.approved.ingestion.document.full_text not in message


def test_the_ai_cannot_change_a_finding(findings, rules, inputs):
    before = [(f.id, f.severity, f.result, f.approved_value, f.revised_value) for f in findings]
    explain(findings, rules, good(inputs))
    assert before == [(f.id, f.severity, f.result, f.approved_value, f.revised_value) for f in findings]


def test_findings_the_model_skipped_get_a_deterministic_item(findings, rules, inputs):
    partial = json.loads(good(inputs))
    partial["findings"] = partial["findings"][:1]
    result, _ = explain(findings, rules, json.dumps(partial))
    assert result.source == AI_ASSISTED
    assert [i.generated_by for i in result.items] == [AI_ASSISTED, DETERMINISTIC, DETERMINISTIC]
    assert "2 finding(s) were not explained" in result.note


# --- errors map to the fallback, with safe messages ----------------------------------------------------------
@pytest.mark.parametrize("error, fragment", [
    (AiTimeout("The Claude API did not answer within 5 seconds."), "did not answer"),
    (AiRequestError("The Claude API rate limit was reached. Try again later."), "rate limit"),
    (AiUnavailable("The 'anthropic' package is not installed."), "not installed"),
])
def test_provider_failures_fall_back_and_say_why(findings, rules, error, fragment):
    result, _ = explain(findings, rules, error=error)
    assert result.source == DETERMINISTIC and fragment in result.note and result.items


class FakeClient:
    def __init__(self, response=None, error=None):
        self.messages = SimpleNamespace(create=self._create)
        self.response, self.error, self.kwargs = response, error, None

    def _create(self, **kwargs):
        self.kwargs = kwargs
        if self.error:
            raise self.error
        return self.response


def reply_with(text):
    return SimpleNamespace(content=[SimpleNamespace(type="text", text=text)])


class APITimeoutError(Exception): ...
class APIConnectionError(Exception): ...
class APIStatusError(Exception):
    def __init__(self, status, text="SECRET-DETAIL " + SENTINEL_KEY):
        super().__init__(text)
        self.status_code = status


@pytest.mark.parametrize("error, kind, fragment", [
    (APITimeoutError("slow"), AiTimeout, "within 5 seconds"),
    (TimeoutError(), AiTimeout, "within 5 seconds"),
    (APIConnectionError("dns"), AiRequestError, "network"),
    (APIStatusError(401), AiRequestError, "credentials"),
    (APIStatusError(429), AiRequestError, "rate limit"),
    (APIStatusError(500), AiRequestError, "service problem"),
    (APIStatusError(400), AiRequestError, "rejected the request"),
    (ValueError("weird"), AiRequestError, "ValueError"),
])
def test_sdk_errors_are_mapped_to_safe_messages(error, kind, fragment):
    provider = ClaudeProvider(SETTINGS, client=FakeClient(error=error))
    with pytest.raises(kind, match=fragment) as caught:
        provider.complete("s", "u")
    assert "SECRET-DETAIL" not in str(caught.value) and SENTINEL_KEY not in str(caught.value)


def test_errors_never_put_the_key_or_sdk_text_in_the_logs(caplog):
    caplog.set_level(logging.DEBUG)
    provider = ClaudeProvider(SETTINGS, client=FakeClient(error=APIStatusError(401)))
    with pytest.raises(AiRequestError):
        provider.complete("system", "user")
    assert caplog.records and SENTINEL_KEY not in caplog.text and "SECRET-DETAIL" not in caplog.text


def test_provider_passes_model_limits_and_only_the_prompt_to_the_client():
    client = FakeClient(reply_with("hello"))
    assert ClaudeProvider(SETTINGS, client=client).complete("SYS", "USR") == "hello"
    assert client.kwargs["model"] == "test-model" and client.kwargs["system"] == "SYS"
    assert client.kwargs["messages"] == [{"role": "user", "content": "USR"}] and client.kwargs["max_tokens"] == 1500
    assert SENTINEL_KEY not in json.dumps(client.kwargs, default=str)


def test_empty_model_output_is_an_error():
    with pytest.raises(AiResponseError, match="no text"):
        ClaudeProvider(SETTINGS, client=FakeClient(SimpleNamespace(content=[]))).complete("s", "u")


def test_provider_without_a_key_does_not_create_a_client(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)  # importing it would fail, so it must not be tried
    with pytest.raises(AiUnavailable, match="No API key"):
        ClaudeProvider(AiSettings(api_key="")).complete("s", "u")


def test_missing_sdk_is_reported_not_crashed(monkeypatch):
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(AiUnavailable, match="pip install anthropic"):
        ClaudeProvider(SETTINGS).complete("s", "u")


# --- malformed and ungrounded answers are rejected as a whole ----------------------------------------------------
def rejected(findings, rules, reply):
    result, _ = explain(findings, rules, reply)
    assert result.source == DETERMINISTIC and "rejected by validation" in result.note, result.note
    assert all(i.generated_by == DETERMINISTIC for i in result.items)
    return result


@pytest.mark.parametrize("reply", [
    "I think the labels look fine.", "", "[1, 2]", '{"summary": "x"', "null",
    '{"summary": "x", "findings": [], "reviewer_questions": [], "extra": 1}',
    '{"summary": "x", "findings": []}',
])
def test_malformed_responses_are_rejected(findings, rules, reply):
    rejected(findings, rules, reply)


def test_json_in_a_code_fence_is_accepted_but_wrong_types_are_not(findings, rules, inputs):
    ok, _ = explain(findings, rules, "```json\n" + good(inputs) + "\n```")
    assert ok.source == AI_ASSISTED
    rejected(findings, rules, good(inputs, summary=5))
    rejected(findings, rules, good(inputs, findings="nope"))
    rejected(findings, rules, good(inputs, reviewer_questions="nope"))


def test_unknown_finding_ids_are_rejected(findings, rules, inputs):
    data = json.loads(good(inputs))
    data["findings"][0]["finding_id"] = "R-FAKE-00000000"
    result = rejected(findings, rules, json.dumps(data))
    assert "was not in the input" in result.note


def test_an_explanation_must_cite_evidence_that_belongs_to_its_finding(findings, rules, inputs):
    none = json.loads(good(inputs)); none["findings"][0]["evidence_refs"] = []
    assert "not grounded" in rejected(findings, rules, json.dumps(none)).note
    foreign = json.loads(good(inputs)); foreign["findings"][0]["evidence_refs"] = [inputs[1].refs[0]]
    assert "does not belong" in rejected(findings, rules, json.dumps(foreign)).note
    invented = json.loads(good(inputs)); invented["findings"][0]["evidence_refs"] = [inputs[0].finding_id + "#E99"]
    rejected(findings, rules, json.dumps(invented))


def test_duplicate_and_excess_explanations_are_rejected(findings, rules, inputs):
    data = json.loads(good(inputs))
    data["findings"].append(dict(data["findings"][0]))
    rejected(findings, rules, json.dumps(data))
    data["findings"] = data["findings"] * 3
    rejected(findings, rules, json.dumps(data))


@pytest.mark.parametrize("claim", [
    "The label is compliant with requirements.", "This is non-compliant.", "The revised label conforms to the approved one.",
    "The label can be approved.", "I recommend approval of the revised label.", "It is ready for release.",
    "The change is safe.", "This is acceptable.", "There is no safety concern here.",
    "This could harm a patient.", "There is a hazard to users.",
])
def test_compliance_approval_release_and_safety_claims_are_rejected(findings, rules, inputs, claim):
    data = json.loads(good(inputs)); data["findings"][0]["explanation"] = claim
    assert rejected(findings, rules, json.dumps(data)).note
    data = json.loads(good(inputs)); data["summary"] = claim
    rejected(findings, rules, json.dumps(data))
    data = json.loads(good(inputs)); data["reviewer_questions"] = [claim]
    rejected(findings, rules, json.dumps(data))


@pytest.mark.parametrize("text", [
    "This breaks 21 CFR Part 820.", "ISO 13485 requires this.", "EU MDR Article 10 applies.", "The FDA requires a new filing.",
])
def test_invented_regulatory_requirements_are_rejected(findings, rules, inputs, text):
    data = json.loads(good(inputs)); data["findings"][0]["explanation"] = text
    assert "cites" in rejected(findings, rules, json.dumps(data)).note


def test_invented_values_numbers_and_quotes_are_rejected(findings, rules, inputs):
    for text in ["The new identifier is NW-IV200-99.", 'The label says "Totally different words here".',
                 "The expiry is 2031-01-01.", "There are 42 changes."]:
        data = json.loads(good(inputs)); data["findings"][0]["explanation"] = text
        rejected(findings, rules, json.dumps(data))


def test_values_that_really_are_in_the_evidence_may_be_repeated(findings, rules, inputs):
    shown = next(f for f in inputs if f.revised_value and any(c.isdigit() for c in f.revised_value))
    data = json.loads(good(inputs))
    entry = next(e for e in data["findings"] if e["finding_id"] == shown.finding_id)
    entry["explanation"] = f"The revised label now shows {shown.revised_value}."
    result, _ = explain(findings, rules, json.dumps(data))
    assert result.source == AI_ASSISTED, result.note


def test_overlong_text_is_rejected(findings, rules, inputs):
    rejected(findings, rules, good(inputs, summary="word " * 300))


def test_validator_reports_every_reason_not_just_the_first(inputs):
    data = json.loads(good(inputs)); data["summary"] = "The label is compliant."
    data["findings"][0]["finding_id"] = "X-1"
    with pytest.raises(AiResponseError) as caught:
        validate.validate_response(json.dumps(data), inputs)
    assert len(caught.value.reasons) >= 2


def test_a_rejected_answer_does_not_leave_partly_trusted_text(findings, rules, inputs):
    data = json.loads(good(inputs)); data["findings"][2]["explanation"] = "This is compliant."
    result = rejected(findings, rules, json.dumps(data))
    assert "AI-assisted" not in result.label and not any(i.generated_by == AI_ASSISTED for i in result.items)


# --- the AI has no way to decide ------------------------------------------------------------------------------------
def test_the_ai_package_cannot_touch_the_database_or_reviews():
    root = Path(__file__).resolve().parents[1] / "labelguard" / "ai"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert not re.search(r"labelguard\.db|from labelguard import db|set_review|sqlite3|review_status", source), path.name


def test_an_explanation_changes_no_review_status_and_is_audited(sample_verification):
    from labelguard import workflow

    result = sample_verification
    job_id = database.save_run(result.analysis, report=result.report)
    findings = tuple(result.report.flagged[:2])
    explanation, uid = workflow.generate_explanation(job_id, findings, list(result.rules))
    assert uid.startswith("EXP-") and explanation.source == DETERMINISTIC
    a, r = result.analysis.approved.document.sha256, result.analysis.revised.document.sha256
    assert database.get_reviews(a, r) == {}  # no decision was created by explaining
    assert database.review_status_counts()["resolved"] == 0
    (event,) = audit.list_events(event_types=[audit.AI_EXPLANATION_CREATED])
    assert event.details["source"] == DETERMINISTIC and event.job_uid


def test_ai_explanations_are_stored_and_read_back_with_their_label(sample_verification):
    from labelguard import workflow
    from labelguard.db import explanations

    result = sample_verification
    job_id = database.save_run(result.analysis, report=result.report)
    findings = tuple(result.report.flagged[:2])
    inputs = prompt.build_inputs(findings, list(result.rules))[0]
    explanation, _ = workflow.generate_explanation(job_id, findings, list(result.rules), mode="ai", provider=FakeProvider(good(inputs)))
    assert explanation.source == AI_ASSISTED
    (stored,) = explanations.list_explanations(job_id)
    assert stored.explanation == explanation and stored.explanation.label == "AI-assisted"
    (event,) = audit.list_events(event_types=[audit.AI_EXPLANATION_CREATED])
    assert "AI-assisted" in event.summary


def test_no_secret_is_stored_in_the_database_or_the_repository(sample_verification, monkeypatch):
    import sqlite3
    from contextlib import closing

    from labelguard import config, workflow

    monkeypatch.setenv("ANTHROPIC_API_KEY", SENTINEL_KEY)
    result = sample_verification
    job_id = database.save_run(result.analysis, report=result.report)
    workflow.generate_explanation(job_id, tuple(result.report.flagged[:1]), list(result.rules))
    with closing(sqlite3.connect(config.DB_PATH)) as c:
        assert SENTINEL_KEY not in "\n".join(c.iterdump())
    root = Path(__file__).resolve().parents[1]
    leaked = [str(p) for p in root.rglob("*") if p.is_file() and ".venv" not in p.parts and "__pycache__" not in p.parts
              and p.suffix in {".py", ".toml", ".md", ".txt", ".example", ".json", ".yml", ".yaml"}
              and re.search(r"sk-ant-[A-Za-z0-9_-]{20,}", p.read_text(encoding="utf-8", errors="ignore"))]
    assert leaked == []


def test_short_numbers_must_be_grounded_but_plain_counts_are_allowed(findings, rules, inputs):
    """Regression: numbers under three characters used to skip the grounding check ("30 degrees" passed)."""
    counted = json.loads(good(inputs)); counted["summary"] = "The rules flagged 3 findings for the reviewer."
    ok, _ = explain(findings, rules, json.dumps(counted))
    assert ok.source == AI_ASSISTED, ok.note
    for text in ["Store at 30 degrees.", "There are 42 changes.", "It changed by 7 units."]:
        data = json.loads(good(inputs)); data["findings"][0]["explanation"] = text
        assert "not in the input" in rejected(findings, rules, json.dumps(data)).note, text
