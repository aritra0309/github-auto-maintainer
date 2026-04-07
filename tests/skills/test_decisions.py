from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_auto_maintainer.core.errors import SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMResponse
from github_auto_maintainer.skills.decisions import (
    IssueTriageDecision,
    PRTriageDecision,
    make_decision_validator,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _golden_pr() -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / "pr_triage_golden.json").read_text())
    return data


def _golden_issue() -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / "issue_triage_golden.json").read_text())
    return data


# ── PRTriageDecision ──────────────────────────────────────────────────


def test_pr_triage_from_valid_json() -> None:
    content = json.dumps(_golden_pr())
    decision = PRTriageDecision.from_llm_response(content)
    assert decision.priority == "medium"
    assert decision.category == "bug_fix"
    assert decision.risk_assessment == "medium"
    assert isinstance(decision.suggested_labels, tuple)
    assert isinstance(decision.suggested_reviewers, tuple)
    assert len(decision.summary) > 0


def test_pr_triage_rejects_invalid_json() -> None:
    with pytest.raises(SkillResponseParsingError, match="Invalid JSON"):
        PRTriageDecision.from_llm_response("not json at all")


def test_pr_triage_rejects_non_object() -> None:
    with pytest.raises(SkillResponseParsingError, match="Expected JSON object"):
        PRTriageDecision.from_llm_response("[1, 2, 3]")


def test_pr_triage_rejects_missing_fields() -> None:
    data = _golden_pr()
    del data["priority"]
    with pytest.raises(SkillResponseParsingError, match="Missing required"):
        PRTriageDecision.from_llm_response(json.dumps(data))


def test_pr_triage_rejects_invalid_priority() -> None:
    data = _golden_pr()
    data["priority"] = "urgent"
    with pytest.raises(SkillResponseParsingError, match="priority"):
        PRTriageDecision.from_llm_response(json.dumps(data))


def test_pr_triage_rejects_invalid_category() -> None:
    data = _golden_pr()
    data["category"] = "hotfix"
    with pytest.raises(SkillResponseParsingError, match="category"):
        PRTriageDecision.from_llm_response(json.dumps(data))


def test_pr_triage_rejects_invalid_risk() -> None:
    data = _golden_pr()
    data["risk_assessment"] = "extreme"
    with pytest.raises(SkillResponseParsingError, match="risk_assessment"):
        PRTriageDecision.from_llm_response(json.dumps(data))


def test_pr_triage_rejects_non_string_labels() -> None:
    data = _golden_pr()
    data["suggested_labels"] = [1, 2]
    with pytest.raises(SkillResponseParsingError, match="suggested_labels"):
        PRTriageDecision.from_llm_response(json.dumps(data))


def test_pr_triage_ignores_unknown_fields() -> None:
    data = _golden_pr()
    data["extra_field"] = "ignored"
    decision = PRTriageDecision.from_llm_response(json.dumps(data))
    assert decision.priority == "medium"


# ── IssueTriageDecision ───────────────────────────────────────────────


def test_issue_triage_from_valid_json() -> None:
    content = json.dumps(_golden_issue())
    decision = IssueTriageDecision.from_llm_response(content)
    assert decision.priority == "high"
    assert decision.category == "bug_report"
    assert decision.needs_more_info is False
    assert isinstance(decision.suggested_labels, tuple)


def test_issue_triage_rejects_missing_fields() -> None:
    data = _golden_issue()
    del data["needs_more_info"]
    with pytest.raises(SkillResponseParsingError, match="Missing required"):
        IssueTriageDecision.from_llm_response(json.dumps(data))


def test_issue_triage_rejects_invalid_category() -> None:
    data = _golden_issue()
    data["category"] = "support_ticket"
    with pytest.raises(SkillResponseParsingError, match="category"):
        IssueTriageDecision.from_llm_response(json.dumps(data))


def test_issue_triage_rejects_non_bool_needs_more_info() -> None:
    data = _golden_issue()
    data["needs_more_info"] = "yes"
    with pytest.raises(SkillResponseParsingError, match="needs_more_info"):
        IssueTriageDecision.from_llm_response(json.dumps(data))


# ── Validator factory ─────────────────────────────────────────────────


def test_validator_returns_true_for_valid_response() -> None:
    validator = make_decision_validator(PRTriageDecision)
    response = LLMResponse(
        content=json.dumps(_golden_pr()),
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is True


def test_validator_returns_false_for_invalid_response() -> None:
    validator = make_decision_validator(PRTriageDecision)
    response = LLMResponse(
        content="not valid json",
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is False


def test_validator_returns_false_for_missing_fields() -> None:
    validator = make_decision_validator(IssueTriageDecision)
    response = LLMResponse(
        content=json.dumps({"priority": "high"}),
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is False
