"""Tests for typed payload extraction helpers."""

from __future__ import annotations

import pytest

from github_auto_maintainer.core.errors import SkillExecutionError
from github_auto_maintainer.skills.payload import (
    extract_issue_number,
    extract_pull_request_number,
    extract_repository_name,
    extract_repository_owner,
    extract_sender_login,
)


def _pr_payload() -> dict[str, object]:
    return {
        "action": "opened",
        "pull_request": {"number": 42},
        "repository": {
            "name": "hello-world",
            "owner": {"login": "octocat"},
        },
        "sender": {"login": "contributor1"},
    }


def _issue_payload() -> dict[str, object]:
    return {
        "action": "opened",
        "issue": {"number": 101},
        "repository": {
            "name": "hello-world",
            "owner": {"login": "octocat"},
        },
        "sender": {"login": "reporter1"},
    }


# ── Valid extraction ──────────────────────────────────────────────────────


def test_extract_repository_owner() -> None:
    assert extract_repository_owner(_pr_payload()) == "octocat"


def test_extract_repository_name() -> None:
    assert extract_repository_name(_pr_payload()) == "hello-world"


def test_extract_pull_request_number() -> None:
    assert extract_pull_request_number(_pr_payload()) == 42


def test_extract_issue_number() -> None:
    assert extract_issue_number(_issue_payload()) == 101


def test_extract_sender_login() -> None:
    assert extract_sender_login(_pr_payload()) == "contributor1"


# ── Missing keys ──────────────────────────────────────────────────────────


def test_missing_repository_key() -> None:
    with pytest.raises(SkillExecutionError, match="repository"):
        extract_repository_owner({})


def test_missing_owner_key() -> None:
    with pytest.raises(SkillExecutionError, match="repository.owner"):
        extract_repository_owner({"repository": {}})


def test_missing_login_key() -> None:
    with pytest.raises(SkillExecutionError, match="repository.owner.login"):
        extract_repository_owner({"repository": {"owner": {}}})


def test_missing_pull_request_key() -> None:
    with pytest.raises(SkillExecutionError, match="pull_request"):
        extract_pull_request_number({})


def test_missing_issue_key() -> None:
    with pytest.raises(SkillExecutionError, match="issue"):
        extract_issue_number({})


def test_missing_sender_key() -> None:
    with pytest.raises(SkillExecutionError, match="sender"):
        extract_sender_login({})


# ── Wrong types ───────────────────────────────────────────────────────────


def test_repository_owner_not_string() -> None:
    payload: dict[str, object] = {"repository": {"owner": {"login": 123}}}
    with pytest.raises(SkillExecutionError, match="Expected string"):
        extract_repository_owner(payload)


def test_pull_request_number_not_int() -> None:
    payload: dict[str, object] = {"pull_request": {"number": "42"}}
    with pytest.raises(SkillExecutionError, match="Expected integer"):
        extract_pull_request_number(payload)


def test_pull_request_number_is_bool() -> None:
    payload: dict[str, object] = {"pull_request": {"number": True}}
    with pytest.raises(SkillExecutionError, match="Expected integer"):
        extract_pull_request_number(payload)


def test_repository_not_a_mapping() -> None:
    payload: dict[str, object] = {"repository": "not-a-dict"}
    with pytest.raises(SkillExecutionError, match="Expected mapping"):
        extract_repository_owner(payload)


def test_empty_string_value() -> None:
    payload: dict[str, object] = {"repository": {"owner": {"login": "  "}}}
    with pytest.raises(SkillExecutionError, match="Empty string"):
        extract_repository_owner(payload)
