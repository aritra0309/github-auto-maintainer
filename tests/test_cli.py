"""Tests for the single-shot CLI mode."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from github_auto_maintainer.cli import (
    _load_event_payload,
    _resolve_github_event,
    process_event,
)


# ---------------------------------------------------------------------------
# _load_event_payload
# ---------------------------------------------------------------------------


def test_load_event_payload_valid(tmp_path: Path) -> None:
    event_file = tmp_path / "event.json"
    payload = {"action": "opened", "issue": {"number": 42}}
    event_file.write_text(json.dumps(payload))

    result = _load_event_payload(str(event_file))
    assert result == payload


def test_load_event_payload_missing_file(tmp_path: Path) -> None:
    with pytest.raises(SystemExit):
        _load_event_payload(str(tmp_path / "nonexistent.json"))


def test_load_event_payload_invalid_json(tmp_path: Path) -> None:
    event_file = tmp_path / "bad.json"
    event_file.write_text("not json {{{")

    with pytest.raises(SystemExit):
        _load_event_payload(str(event_file))


def test_load_event_payload_non_object(tmp_path: Path) -> None:
    event_file = tmp_path / "array.json"
    event_file.write_text("[1, 2, 3]")

    with pytest.raises(SystemExit):
        _load_event_payload(str(event_file))


# ---------------------------------------------------------------------------
# _resolve_github_event
# ---------------------------------------------------------------------------


def test_resolve_github_event_valid() -> None:
    assert _resolve_github_event("issues") == "issues"
    assert _resolve_github_event("  Pull_Request  ") == "pull_request"


def test_resolve_github_event_missing() -> None:
    with pytest.raises(SystemExit):
        _resolve_github_event(None)

    with pytest.raises(SystemExit):
        _resolve_github_event("")


# ---------------------------------------------------------------------------
# process_event (integration-style)
# ---------------------------------------------------------------------------


def test_process_event_missing_app_id(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """process_event exits when GITHUB_APP_ID is not set."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"action": "opened"}))

    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/fake.pem")
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issues")

    with pytest.raises(SystemExit):
        process_event(str(event_file))


def test_process_event_missing_key_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """process_event exits when GITHUB_APP_PRIVATE_KEY_PATH is not set."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"action": "opened"}))

    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)
    monkeypatch.setenv("GITHUB_EVENT_NAME", "issues")

    with pytest.raises(SystemExit):
        process_event(str(event_file))


def test_process_event_missing_event_name(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """process_event exits when GITHUB_EVENT_NAME is not set."""
    event_file = tmp_path / "event.json"
    event_file.write_text(json.dumps({"action": "opened"}))

    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/fake.pem")
    monkeypatch.delenv("GITHUB_EVENT_NAME", raising=False)

    with pytest.raises(SystemExit):
        process_event(str(event_file))
