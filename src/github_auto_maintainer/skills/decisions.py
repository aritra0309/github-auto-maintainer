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

_CONFIDENCE_LEVELS = frozenset({"high", "medium", "low"})
_PATCH_ACTIONS = frozenset({"modify", "create", "delete"})


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


# ── Phase 5 decision types ───────────────────────────────────


@dataclass(frozen=True, slots=True)
class PatchFileSpec:
    """Specification for a single file in a patch."""

    path: str
    action: Literal["modify", "create", "delete"]
    new_content: str
    reasoning: str


@dataclass(frozen=True, slots=True)
class PatchGenerationDecision:
    """Parsed patch generation decision from LLM output."""

    can_fix: bool
    rejection_reason: str | None
    files_to_modify: tuple[PatchFileSpec, ...]
    commit_message: str
    confidence: Literal["high", "medium", "low"]
    explanation: str

    @classmethod
    def from_llm_response(cls, content: str) -> Self:
        data = _parse_json_object(content)
        _validate_required_fields(
            data,
            required=(
                "can_fix", "rejection_reason", "files_to_modify",
                "commit_message", "confidence", "explanation",
            ),
        )
        can_fix = _validate_bool(data, "can_fix")
        confidence = _validate_enum(data, "confidence", _CONFIDENCE_LEVELS)
        explanation = _validate_string(data, "explanation")

        rejection_reason: str | None
        raw_reason = data["rejection_reason"]
        if raw_reason is None:
            rejection_reason = None
        elif isinstance(raw_reason, str):
            rejection_reason = raw_reason
        else:
            raise SkillResponseParsingError(
                f"Field 'rejection_reason' must be a string or null, "
                f"got {type(raw_reason).__name__}"
            )

        commit_message: str
        raw_commit = data["commit_message"]
        if not isinstance(raw_commit, str):
            raise SkillResponseParsingError(
                f"Field 'commit_message' must be a string, "
                f"got {type(raw_commit).__name__}"
            )
        commit_message = raw_commit

        files_to_modify: tuple[PatchFileSpec, ...]
        if can_fix:
            files_to_modify = _validate_patch_file_spec_list(data)
        else:
            # When can_fix is False, files_to_modify should be empty
            raw_files = data["files_to_modify"]
            if not isinstance(raw_files, list):
                raise SkillResponseParsingError(
                    f"Field 'files_to_modify' must be a list, "
                    f"got {type(raw_files).__name__}"
                )
            files_to_modify = ()

        return cls(
            can_fix=can_fix,
            rejection_reason=rejection_reason,
            files_to_modify=files_to_modify,
            commit_message=commit_message,
            confidence=confidence,  # type: ignore[arg-type]
            explanation=explanation,
        )


def make_decision_validator(
    decision_cls: type[PRTriageDecision] | type[IssueTriageDecision]
    | type[PRSummaryDecision] | type[IssueLabelDecision] | type[IssueResponseDecision]
    | type[PatchGenerationDecision],
) -> Callable[[LLMResponse], bool]:
    """Build a ResponseValidator that returns True on successful parse, False otherwise."""

    def validator(response: LLMResponse) -> bool:
        try:
            decision_cls.from_llm_response(response.content)
        except SkillResponseParsingError:
            return False
        return True

    return validator


# ── Internal validation helpers ───────────────────────────────


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


def _validate_patch_file_spec_list(
    data: dict[str, object],
) -> tuple[PatchFileSpec, ...]:
    """Parse and validate the files_to_modify array."""
    raw = data["files_to_modify"]
    if not isinstance(raw, list):
        raise SkillResponseParsingError(
            f"Field 'files_to_modify' must be a list, got {type(raw).__name__}"
        )
    specs: list[PatchFileSpec] = []
    for idx, item in enumerate(raw):
        if not isinstance(item, dict):
            raise SkillResponseParsingError(
                f"files_to_modify[{idx}] must be an object, got {type(item).__name__}"
            )
        required_keys = ("path", "action", "new_content", "reasoning")
        missing = [k for k in required_keys if k not in item]
        if missing:
            raise SkillResponseParsingError(
                f"files_to_modify[{idx}] missing required fields: {missing}"
            )
        path = item["path"]
        if not isinstance(path, str):
            raise SkillResponseParsingError(
                f"files_to_modify[{idx}].path must be a string"
            )
        action = item["action"]
        if not isinstance(action, str) or action not in _PATCH_ACTIONS:
            raise SkillResponseParsingError(
                f"files_to_modify[{idx}].action must be one of {sorted(_PATCH_ACTIONS)}, "
                f"got '{action}'"
            )
        new_content = item["new_content"]
        if not isinstance(new_content, str):
            raise SkillResponseParsingError(
                f"files_to_modify[{idx}].new_content must be a string"
            )
        reasoning = item["reasoning"]
        if not isinstance(reasoning, str):
            raise SkillResponseParsingError(
                f"files_to_modify[{idx}].reasoning must be a string"
            )
        specs.append(
            PatchFileSpec(
                path=path,
                action=action,  # type: ignore[arg-type]
                new_content=new_content,
                reasoning=reasoning,
            )
        )
    return tuple(specs)
