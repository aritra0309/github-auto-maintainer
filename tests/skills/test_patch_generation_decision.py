"""Tests for PatchGenerationDecision and PatchFileSpec parsing."""

from __future__ import annotations

import json

import pytest

from github_auto_maintainer.core.errors import SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMResponse
from github_auto_maintainer.skills.decisions import (
    PatchFileSpec,
    PatchGenerationDecision,
    make_decision_validator,
)


def _valid_can_fix_json() -> str:
    return json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {
                "path": "src/main.py",
                "action": "modify",
                "new_content": "print('fixed')",
                "reasoning": "Fix the bug",
            }
        ],
        "commit_message": "fix: resolve issue",
        "confidence": "high",
        "explanation": "Simple one-line fix",
    })


def _valid_cannot_fix_json() -> str:
    return json.dumps({
        "can_fix": False,
        "rejection_reason": "Too complex to fix automatically",
        "files_to_modify": [],
        "commit_message": "",
        "confidence": "low",
        "explanation": "This issue requires architectural changes",
    })


# ── from_llm_response: valid ─────────────────────────────────────


def test_parse_valid_can_fix() -> None:
    decision = PatchGenerationDecision.from_llm_response(_valid_can_fix_json())
    assert decision.can_fix is True
    assert decision.rejection_reason is None
    assert len(decision.files_to_modify) == 1
    assert decision.files_to_modify[0].path == "src/main.py"
    assert decision.files_to_modify[0].action == "modify"
    assert decision.files_to_modify[0].new_content == "print('fixed')"
    assert decision.files_to_modify[0].reasoning == "Fix the bug"
    assert decision.commit_message == "fix: resolve issue"
    assert decision.confidence == "high"
    assert decision.explanation == "Simple one-line fix"


def test_parse_valid_cannot_fix() -> None:
    decision = PatchGenerationDecision.from_llm_response(_valid_cannot_fix_json())
    assert decision.can_fix is False
    assert decision.rejection_reason == "Too complex to fix automatically"
    assert decision.files_to_modify == ()
    assert decision.commit_message == ""
    assert decision.confidence == "low"


def test_parse_multiple_files() -> None:
    data = json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {
                "path": "a.py",
                "action": "modify",
                "new_content": "# a",
                "reasoning": "Fix a",
            },
            {
                "path": "b.py",
                "action": "create",
                "new_content": "# b",
                "reasoning": "Add b",
            },
            {
                "path": "c.py",
                "action": "delete",
                "new_content": "",
                "reasoning": "Remove c",
            },
        ],
        "commit_message": "fix: multi-file",
        "confidence": "medium",
        "explanation": "Multi-file fix",
    })
    decision = PatchGenerationDecision.from_llm_response(data)
    assert len(decision.files_to_modify) == 3
    assert decision.files_to_modify[0].action == "modify"
    assert decision.files_to_modify[1].action == "create"
    assert decision.files_to_modify[2].action == "delete"


# ── from_llm_response: invalid ───────────────────────────────────


def test_parse_invalid_json() -> None:
    with pytest.raises(SkillResponseParsingError, match="Invalid JSON"):
        PatchGenerationDecision.from_llm_response("not json")


def test_parse_missing_fields() -> None:
    data = json.dumps({"can_fix": True})
    with pytest.raises(SkillResponseParsingError, match="Missing required fields"):
        PatchGenerationDecision.from_llm_response(data)


def test_parse_invalid_confidence() -> None:
    data = json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [],
        "commit_message": "fix",
        "confidence": "very_high",
        "explanation": "test",
    })
    with pytest.raises(SkillResponseParsingError, match="confidence"):
        PatchGenerationDecision.from_llm_response(data)


def test_parse_invalid_action_in_file_spec() -> None:
    data = json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {
                "path": "a.py",
                "action": "rename",
                "new_content": "# a",
                "reasoning": "test",
            }
        ],
        "commit_message": "fix",
        "confidence": "high",
        "explanation": "test",
    })
    with pytest.raises(SkillResponseParsingError, match="action"):
        PatchGenerationDecision.from_llm_response(data)


def test_parse_missing_file_spec_fields() -> None:
    data = json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {"path": "a.py"}  # missing action, new_content, reasoning
        ],
        "commit_message": "fix",
        "confidence": "high",
        "explanation": "test",
    })
    with pytest.raises(SkillResponseParsingError, match="missing required fields"):
        PatchGenerationDecision.from_llm_response(data)


def test_parse_non_bool_can_fix() -> None:
    data = json.dumps({
        "can_fix": "yes",
        "rejection_reason": None,
        "files_to_modify": [],
        "commit_message": "",
        "confidence": "low",
        "explanation": "test",
    })
    with pytest.raises(SkillResponseParsingError, match="can_fix"):
        PatchGenerationDecision.from_llm_response(data)


def test_parse_non_list_files_to_modify() -> None:
    data = json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": "not a list",
        "commit_message": "fix",
        "confidence": "high",
        "explanation": "test",
    })
    with pytest.raises(SkillResponseParsingError, match="files_to_modify"):
        PatchGenerationDecision.from_llm_response(data)


def test_parse_non_string_rejection_reason() -> None:
    data = json.dumps({
        "can_fix": False,
        "rejection_reason": 42,
        "files_to_modify": [],
        "commit_message": "",
        "confidence": "low",
        "explanation": "test",
    })
    with pytest.raises(SkillResponseParsingError, match="rejection_reason"):
        PatchGenerationDecision.from_llm_response(data)


# ── make_decision_validator ───────────────────────────────────────


def test_validator_returns_true_on_valid() -> None:
    validator = make_decision_validator(PatchGenerationDecision)
    response = LLMResponse(
        content=_valid_can_fix_json(),
        provider="fake",
        model="fake-model",
        input_tokens=100,
        output_tokens=50,
    )
    assert validator(response) is True


def test_validator_returns_false_on_invalid() -> None:
    validator = make_decision_validator(PatchGenerationDecision)
    response = LLMResponse(
        content="not json",
        provider="fake",
        model="fake-model",
        input_tokens=100,
        output_tokens=50,
    )
    assert validator(response) is False


# ── PatchFileSpec construction ────────────────────────────────────


def test_patch_file_spec_construction() -> None:
    spec = PatchFileSpec(
        path="src/main.py",
        action="modify",
        new_content="# content",
        reasoning="fix bug",
    )
    assert spec.path == "src/main.py"
    assert spec.action == "modify"
    assert spec.new_content == "# content"
    assert spec.reasoning == "fix bug"
