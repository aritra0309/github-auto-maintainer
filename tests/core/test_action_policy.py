"""Tests for ActionPolicy: DRY_RUN mode and allowlists."""

from __future__ import annotations

import pytest

from github_auto_maintainer.core.action_policy import ActionPolicy


def test_dry_run_defaults_true() -> None:
    policy = ActionPolicy()
    assert policy.dry_run is True


def test_dry_run_explicit_false() -> None:
    policy = ActionPolicy(dry_run=False)
    assert policy.dry_run is False


def test_dry_run_env_false(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "false")
    policy = ActionPolicy()
    assert policy.dry_run is False


def test_dry_run_env_true(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("DRY_RUN", "true")
    policy = ActionPolicy()
    assert policy.dry_run is True


def test_empty_repo_allowlist_allows_any() -> None:
    policy = ActionPolicy(allowed_repositories=())
    assert policy.is_repo_allowed("any/repo") is True


def test_repo_in_allowlist_allowed() -> None:
    policy = ActionPolicy(allowed_repositories=("owner/repo",))
    assert policy.is_repo_allowed("owner/repo") is True


def test_repo_not_in_allowlist_denied() -> None:
    policy = ActionPolicy(allowed_repositories=("owner/repo",))
    assert policy.is_repo_allowed("other/repo") is False


def test_none_repo_with_nonempty_allowlist_denied() -> None:
    policy = ActionPolicy(allowed_repositories=("owner/repo",))
    assert policy.is_repo_allowed(None) is False


def test_empty_event_allowlist_allows_any() -> None:
    policy = ActionPolicy(allowed_events=())
    assert policy.is_event_allowed("issues.opened") is True


def test_event_in_allowlist_allowed() -> None:
    policy = ActionPolicy(allowed_events=("issues.opened",))
    assert policy.is_event_allowed("issues.opened") is True


def test_event_not_in_allowlist_denied() -> None:
    policy = ActionPolicy(allowed_events=("issues.opened",))
    assert policy.is_event_allowed("pull_request.opened") is False
