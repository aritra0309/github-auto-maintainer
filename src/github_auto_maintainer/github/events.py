"""GitHub webhook event normalization into internal envelopes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any


@dataclass(frozen=True, slots=True)
class NormalizedEvent:
    """Internal event contract derived from a GitHub webhook payload."""

    event_name: str
    delivery_id: str
    github_event: str
    action: str
    installation_id: int | None
    repository_full_name: str | None
    repository_id: int | None
    received_at: datetime
    payload: dict[str, Any]


def normalize_github_event(
    *,
    github_event: str,
    delivery_id: str,
    payload: dict[str, Any],
    received_at: datetime | None = None,
) -> NormalizedEvent:
    """Convert GitHub webhook inputs into a deterministic internal envelope."""

    normalized_github_event = github_event.strip().lower()
    action = _coerce_action(payload.get("action"))
    event_name = f"{normalized_github_event}.{action}"

    repository = _as_mapping(payload.get("repository"))
    installation = _as_mapping(payload.get("installation"))

    return NormalizedEvent(
        event_name=event_name,
        delivery_id=delivery_id,
        github_event=normalized_github_event,
        action=action,
        installation_id=_coerce_int(installation.get("id")),
        repository_full_name=_coerce_str(repository.get("full_name")),
        repository_id=_coerce_int(repository.get("id")),
        received_at=received_at or datetime.now(tz=UTC),
        payload=payload,
    )


def _coerce_action(value: object) -> str:
    if isinstance(value, str):
        stripped = value.strip().lower()
        if stripped:
            return stripped
    return "unknown"


def _coerce_int(value: object) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, str) and value.isdigit():
        return int(value)
    return None


def _coerce_str(value: object) -> str | None:
    if isinstance(value, str):
        stripped = value.strip()
        if stripped:
            return stripped
    return None


def _as_mapping(value: object) -> dict[str, Any]:
    if isinstance(value, dict):
        converted: dict[str, Any] = {}
        for key, child_value in value.items():
            converted[str(key)] = child_value
        return converted
    return {}
