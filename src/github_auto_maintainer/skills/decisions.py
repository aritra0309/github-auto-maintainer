"""Strict typed decision parsing for skill LLM outputs."""

from __future__ import annotations

import json
from collections.abc import Callable
from dataclasses import dataclass
from typing import Literal, Self

from github_auto_maintainer.core.errors import SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMResponse

_PR_PRIORITIES = frozenset({"critical", "high", "medium", "low"})
_PR_CATEGORIES = frozenset({"bug_fix", "feature", "refactor", "docs", "test", "ci", "dependency"})
_RISK_LEVELS = frozenset({"high", "medium", "low"})

_ISSUE_PRIORITIES = frozenset({"critical", "high", "medium", "low"})
_ISSUE_CATEGORIES = frozenset(
    {"bug_report", "feature_request", "question", "documentation", "enhancement"}
)


@dataclass(frozen=True, slots=True)
class PRTriageDecision:
    """Parsed PR triage decision from LLM output."""

    priority: Literal["critical", "high", "medium", "low"]
    category: Literal["bug_fix", "feature", "refactor", "docs", "test", "ci", "dependency"]
    suggested_labels: tuple[str, ...]
    suggested_reviewers: tuple[str, ...]
    risk_assessment: Literal["high", "medium", "low"]
    summary: str

    @classmethod
    def from_llm_response(cls, content: str) -> Self:
        data = _parse_json_object(content)
        _validate_required_fields(
            data,
            required=("priority", "category", "suggested_labels", "suggested_reviewers",
                      "risk_assessment", "summary"),
        )
        priority = _validate_enum(data, "priority", _PR_PRIORITIES)
        category = _validate_enum(data, "category", _PR_CATEGORIES)
        suggested_labels = _validate_string_list(data, "suggested_labels")
        suggested_reviewers = _validate_string_list(data, "suggested_reviewers")
        risk_assessment = _validate_enum(data, "risk_assessment", _RISK_LEVELS)
        summary = _validate_string(data, "summary")

        return cls(
            priority=priority,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            suggested_labels=suggested_labels,
            suggested_reviewers=suggested_reviewers,
            risk_assessment=risk_assessment,  # type: ignore[arg-type]
            summary=summary,
        )


@dataclass(frozen=True, slots=True)
class IssueTriageDecision:
    """Parsed issue triage decision from LLM output."""

    priority: Literal["critical", "high", "medium", "low"]
    category: Literal["bug_report", "feature_request", "question", "documentation", "enhancement"]
    suggested_labels: tuple[str, ...]
    needs_more_info: bool
    summary: str

    @classmethod
    def from_llm_response(cls, content: str) -> Self:
        data = _parse_json_object(content)
        _validate_required_fields(
            data,
            required=("priority", "category", "suggested_labels", "needs_more_info", "summary"),
        )
        priority = _validate_enum(data, "priority", _ISSUE_PRIORITIES)
        category = _validate_enum(data, "category", _ISSUE_CATEGORIES)
        suggested_labels = _validate_string_list(data, "suggested_labels")
        needs_more_info = _validate_bool(data, "needs_more_info")
        summary = _validate_string(data, "summary")

        return cls(
            priority=priority,  # type: ignore[arg-type]
            category=category,  # type: ignore[arg-type]
            suggested_labels=suggested_labels,
            needs_more_info=needs_more_info,
            summary=summary,
        )


@dataclass(frozen=True, slots=True)
class PRSummaryDecision:
    """Parsed PR summary review decision from LLM output."""

    summary: str
    key_changes: tuple[str, ...]
    suggestions: tuple[str, ...]
    risk_level: Literal["high", "medium", "low"]

    @classmethod
    def from_llm_response(cls, content: str) -> Self:
        data = _parse_json_object(content)
        _validate_required_fields(
            data,
            required=("summary", "key_changes", "suggestions", "risk_level"),
        )
        summary = _validate_string(data, "summary")
        key_changes = _validate_string_list(data, "key_changes")
        suggestions = _validate_string_list(data, "suggestions")
        risk_level = _validate_enum(data, "risk_level", _RISK_LEVELS)

        return cls(
            summary=summary,
            key_changes=key_changes,
            suggestions=suggestions,
            risk_level=risk_level,  # type: ignore[arg-type]
        )


@dataclass(frozen=True, slots=True)
class IssueLabelDecision:
    """Parsed issue labeling decision from LLM output."""

    labels: tuple[str, ...]
    reasoning: str

    @classmethod
    def from_llm_response(cls, content: str) -> Self:
        data = _parse_json_object(content)
        _validate_required_fields(
            data,
            required=("labels", "reasoning"),
        )
        labels = _validate_string_list(data, "labels")
        reasoning = _validate_string(data, "reasoning")

        return cls(
            labels=labels,
            reasoning=reasoning,
        )


@dataclass(frozen=True, slots=True)
class IssueResponseDecision:
    """Parsed issue response decision from LLM output."""

    response_body: str
    needs_more_info: bool
    category: Literal["bug_report", "feature_request", "question", "documentation", "enhancement"]

    @classmethod
    def from_llm_response(cls, content: str) -> Self:
        data = _parse_json_object(content)
        _validate_required_fields(
            data,
            required=("response_body", "needs_more_info", "category"),
        )
        response_body = _validate_string(data, "response_body")
        needs_more_info = _validate_bool(data, "needs_more_info")
        category = _validate_enum(data, "category", _ISSUE_CATEGORIES)

        return cls(
            response_body=response_body,
            needs_more_info=needs_more_info,
            category=category,  # type: ignore[arg-type]
        )


def make_decision_validator(
    decision_cls: type[PRTriageDecision] | type[IssueTriageDecision]
    | type[PRSummaryDecision] | type[IssueLabelDecision] | type[IssueResponseDecision],
) -> Callable[[LLMResponse], bool]:
    """Build a ResponseValidator that returns True on successful parse, False otherwise."""

    def validator(response: LLMResponse) -> bool:
        try:
            decision_cls.from_llm_response(response.content)
        except SkillResponseParsingError:
            return False
        return True

    return validator


# ── Internal validation helpers ───────────────────────────────────────


def _parse_json_object(content: str) -> dict[str, object]:
    try:
        data = json.loads(content)
    except (json.JSONDecodeError, ValueError) as exc:
        raise SkillResponseParsingError(f"Invalid JSON: {exc}") from exc
    if not isinstance(data, dict):
        raise SkillResponseParsingError(
            f"Expected JSON object, got {type(data).__name__}"
        )
    return data


def _validate_required_fields(
    data: dict[str, object], *, required: tuple[str, ...]
) -> None:
    missing = [field for field in required if field not in data]
    if missing:
        raise SkillResponseParsingError(f"Missing required fields: {missing}")


def _validate_enum(data: dict[str, object], field: str, allowed: frozenset[str]) -> str:
    value = data[field]
    if not isinstance(value, str):
        raise SkillResponseParsingError(
            f"Field '{field}' must be a string, got {type(value).__name__}"
        )
    if value not in allowed:
        raise SkillResponseParsingError(
            f"Field '{field}' value '{value}' not in {sorted(allowed)}"
        )
    return value


def _validate_string(data: dict[str, object], field: str) -> str:
    value = data[field]
    if not isinstance(value, str):
        raise SkillResponseParsingError(
            f"Field '{field}' must be a string, got {type(value).__name__}"
        )
    return value


def _validate_bool(data: dict[str, object], field: str) -> bool:
    value = data[field]
    if not isinstance(value, bool):
        raise SkillResponseParsingError(
            f"Field '{field}' must be a boolean, got {type(value).__name__}"
        )
    return value


def _validate_string_list(data: dict[str, object], field: str) -> tuple[str, ...]:
    value = data[field]
    if not isinstance(value, list):
        raise SkillResponseParsingError(
            f"Field '{field}' must be a list, got {type(value).__name__}"
        )
    for idx, item in enumerate(value):
        if not isinstance(item, str):
            raise SkillResponseParsingError(
                f"Field '{field}[{idx}]' must be a string, got {type(item).__name__}"
            )
    return tuple(value)
