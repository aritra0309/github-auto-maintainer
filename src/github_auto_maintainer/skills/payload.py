"""Typed payload extraction helpers for GitHub webhook events."""

from __future__ import annotations

from typing import Any

from github_auto_maintainer.core.errors import SkillExecutionError


def extract_repository_owner(payload: dict[str, Any]) -> str:
    """Extract repository owner login from a webhook payload."""
    return _walk_str(payload, ("repository", "owner", "login"))


def extract_repository_name(payload: dict[str, Any]) -> str:
    """Extract repository name from a webhook payload."""
    return _walk_str(payload, ("repository", "name"))


def extract_pull_request_number(payload: dict[str, Any]) -> int:
    """Extract pull request number from a webhook payload."""
    return _walk_int(payload, ("pull_request", "number"))


def extract_issue_number(payload: dict[str, Any]) -> int:
    """Extract issue number from a webhook payload."""
    return _walk_int(payload, ("issue", "number"))


def extract_sender_login(payload: dict[str, Any]) -> str:
    """Extract sender login from a webhook payload."""
    return _walk_str(payload, ("sender", "login"))


# ── Internal path walker ──────────────────────────────────────────────────────


def _walk(payload: dict[str, Any], keys: tuple[str, ...]) -> object:
    """Walk a nested dict by key path, raising SkillExecutionError on failure."""
    current: object = payload
    for i, key in enumerate(keys):
        if not isinstance(current, dict):
            path = ".".join(keys[: i + 1])
            raise SkillExecutionError(
                f"Expected mapping at '{'.'.join(keys[:i])}', "
                f"got {type(current).__name__} while resolving '{path}'"
            )
        if key not in current:
            path = ".".join(keys[: i + 1])
            raise SkillExecutionError(f"Missing required payload field: '{path}'")
        current = current[key]
    return current


def _walk_str(payload: dict[str, Any], keys: tuple[str, ...]) -> str:
    """Walk to a string value, raising SkillExecutionError if not a string."""
    value = _walk(payload, keys)
    if not isinstance(value, str):
        path = ".".join(keys)
        raise SkillExecutionError(
            f"Expected string at '{path}', got {type(value).__name__}"
        )
    if not value.strip():
        path = ".".join(keys)
        raise SkillExecutionError(f"Empty string at '{path}'")
    return value


def _walk_int(payload: dict[str, Any], keys: tuple[str, ...]) -> int:
    """Walk to an int value, raising SkillExecutionError if not an int."""
    value = _walk(payload, keys)
    if isinstance(value, bool) or not isinstance(value, int):
        path = ".".join(keys)
        raise SkillExecutionError(
            f"Expected integer at '{path}', got {type(value).__name__}"
        )
    return value
