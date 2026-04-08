from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_auto_maintainer.core.errors import SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMResponse
from github_auto_maintainer.skills.decisions import (
    IssueLabelDecision,
    IssueResponseDecision,
    PRSummaryDecision,
    make_decision_validator,
)

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _golden_pr_summary() -> dict[str, object]:
    data: dict[str, object] = json.loads(
        (FIXTURES / "pr_summary_golden.json").read_text()
    )
    return data


def _golden_issue_label() -> dict[str, object]:
    data: dict[str, object] = json.loads(
        (FIXTURES / "issue_label_golden.json").read_text()
    )
    return data


def _golden_issue_response() -> dict[str, object]:
    data: dict[str, object] = json.loads(
        (FIXTURES / "issue_response_golden.json").read_text()
    )
    return data


# ── PRSummaryDecision ─────────────────────────────────────────────────


def test_pr_summary_from_valid_json() -> None:
    content = json.dumps(_golden_pr_summary())
    decision = PRSummaryDecision.from_llm_response(content)
    assert decision.summary == (
        "Fixes a null pointer exception in the auth module "
        "by adding token expiration validation before use."
    )
    assert decision.key_changes == (
        "Added null check for token expiration",
        "Updated auth module error handling",
    )
    assert decision.suggestions == (
        "Consider adding a unit test for the expired token path",
    )
    assert decision.risk_level == "medium"


def test_pr_summary_rejects_missing_field() -> None:
    data = _golden_pr_summary()
    del data["risk_level"]
    with pytest.raises(SkillResponseParsingError, match="Missing required"):
        PRSummaryDecision.from_llm_response(json.dumps(data))


def test_pr_summary_rejects_invalid_risk_level() -> None:
    data = _golden_pr_summary()
    data["risk_level"] = "extreme"
    with pytest.raises(SkillResponseParsingError, match="risk_level"):
        PRSummaryDecision.from_llm_response(json.dumps(data))


def test_pr_summary_validator_valid() -> None:
    validator = make_decision_validator(PRSummaryDecision)
    response = LLMResponse(
        content=json.dumps(_golden_pr_summary()),
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is True


def test_pr_summary_validator_invalid() -> None:
    validator = make_decision_validator(PRSummaryDecision)
    response = LLMResponse(
        content="not valid json",
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is False


# ── IssueLabelDecision ────────────────────────────────────────────────


def test_issue_label_from_valid_json() -> None:
    content = json.dumps(_golden_issue_label())
    decision = IssueLabelDecision.from_llm_response(content)
    assert decision.labels == ("bug", "high-priority", "login")
    assert decision.reasoning == (
        "This is a server error (500) on a critical user flow (login), "
        "warranting bug and high-priority labels."
    )


def test_issue_label_rejects_missing_field() -> None:
    data = _golden_issue_label()
    del data["reasoning"]
    with pytest.raises(SkillResponseParsingError, match="Missing required"):
        IssueLabelDecision.from_llm_response(json.dumps(data))


def test_issue_label_rejects_non_string_labels() -> None:
    data = _golden_issue_label()
    data["labels"] = [1, 2]
    with pytest.raises(SkillResponseParsingError, match="labels"):
        IssueLabelDecision.from_llm_response(json.dumps(data))


def test_issue_label_validator_valid() -> None:
    validator = make_decision_validator(IssueLabelDecision)
    response = LLMResponse(
        content=json.dumps(_golden_issue_label()),
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is True


def test_issue_label_validator_invalid() -> None:
    validator = make_decision_validator(IssueLabelDecision)
    response = LLMResponse(
        content=json.dumps({"labels": "not-a-list"}),
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is False


# ── IssueResponseDecision ────────────────────────────────────────────


def test_issue_response_from_valid_json() -> None:
    content = json.dumps(_golden_issue_response())
    decision = IssueResponseDecision.from_llm_response(content)
    assert decision.response_body.startswith("Thank you for reporting")
    assert decision.needs_more_info is True
    assert decision.category == "bug_report"


def test_issue_response_rejects_missing_field() -> None:
    data = _golden_issue_response()
    del data["category"]
    with pytest.raises(SkillResponseParsingError, match="Missing required"):
        IssueResponseDecision.from_llm_response(json.dumps(data))


def test_issue_response_rejects_invalid_category() -> None:
    data = _golden_issue_response()
    data["category"] = "support_ticket"
    with pytest.raises(SkillResponseParsingError, match="category"):
        IssueResponseDecision.from_llm_response(json.dumps(data))


def test_issue_response_rejects_non_bool_needs_more_info() -> None:
    data = _golden_issue_response()
    data["needs_more_info"] = "yes"
    with pytest.raises(SkillResponseParsingError, match="needs_more_info"):
        IssueResponseDecision.from_llm_response(json.dumps(data))


def test_issue_response_validator_valid() -> None:
    validator = make_decision_validator(IssueResponseDecision)
    response = LLMResponse(
        content=json.dumps(_golden_issue_response()),
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is True


def test_issue_response_validator_invalid() -> None:
    validator = make_decision_validator(IssueResponseDecision)
    response = LLMResponse(
        content="not valid json",
        provider="test",
        model="test",
        input_tokens=10,
        output_tokens=5,
    )
    assert validator(response) is False
