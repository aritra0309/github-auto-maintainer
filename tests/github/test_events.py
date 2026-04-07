from __future__ import annotations

from datetime import UTC, datetime

from github_auto_maintainer.github.events import normalize_github_event


def test_normalize_github_event_builds_internal_event_name_and_metadata() -> None:
    received_at = datetime(2026, 4, 6, 12, 0, 0, tzinfo=UTC)
    payload = {
        "action": "opened",
        "installation": {"id": 321},
        "repository": {"id": 456, "full_name": "octo/repo"},
    }

    event = normalize_github_event(
        github_event="pull_request",
        delivery_id="delivery-1",
        payload=payload,
        received_at=received_at,
    )

    assert event.event_name == "pull_request.opened"
    assert event.github_event == "pull_request"
    assert event.action == "opened"
    assert event.delivery_id == "delivery-1"
    assert event.installation_id == 321
    assert event.repository_id == 456
    assert event.repository_full_name == "octo/repo"
    assert event.received_at == received_at


def test_normalize_github_event_defaults_unknown_action_when_missing() -> None:
    event = normalize_github_event(
        github_event="issues",
        delivery_id="delivery-2",
        payload={},
    )

    assert event.event_name == "issues.unknown"
    assert event.action == "unknown"
    assert event.installation_id is None
    assert event.repository_id is None
    assert event.repository_full_name is None
