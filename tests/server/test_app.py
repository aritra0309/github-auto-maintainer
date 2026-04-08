from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from typing import Any

import pytest
from fastapi.testclient import TestClient

from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.server.app import create_app


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_health_endpoint_reports_ok() -> None:
    app = create_app(webhook_secret="test-secret")
    client = TestClient(app)

    response = client.get("/health")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_webhook_enqueues_normalized_event_on_valid_signature() -> None:
    queue = InMemoryJobQueue[NormalizedEvent]()
    app = create_app(queue=queue, webhook_secret="test-secret")
    client = TestClient(app)

    payload = {
        "action": "opened",
        "installation": {"id": 99},
        "repository": {"id": 123, "full_name": "octo/repo"},
    }
    body = json.dumps(payload).encode("utf-8")
    signature = _signature("test-secret", body)

    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-123",
            "X-Hub-Signature-256": signature,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 202
    assert response.json() == {"status": "accepted"}
    assert queue.qsize() == 1

    event = queue.dequeue_nowait()
    assert event.event_name == "pull_request.opened"
    assert event.delivery_id == "delivery-123"
    assert event.installation_id == 99
    assert event.repository_full_name == "octo/repo"


def test_webhook_rejects_invalid_signature() -> None:
    app = create_app(webhook_secret="test-secret")
    client = TestClient(app)
    body = b'{"action":"opened"}'

    response = client.post(
        "/webhook",
        content=body,
        headers={
            "X-GitHub-Event": "pull_request",
            "X-GitHub-Delivery": "delivery-123",
            "X-Hub-Signature-256": "sha256=" + "0" * 64,
            "Content-Type": "application/json",
        },
    )

    assert response.status_code == 401


# ── Lifespan tests ────────────────────────────────────────────────────


class _RecordingDispatcher:
    """Fake Orchestrator that records construction and supports cancellation."""

    constructed: bool = False
    run_called: bool = False

    def __init__(self, **kwargs: Any) -> None:
        _RecordingDispatcher.constructed = True

    async def run(self) -> None:
        _RecordingDispatcher.run_called = True
        try:
            await asyncio.sleep(3600)
        except asyncio.CancelledError:
            return

    @classmethod
    def reset(cls) -> None:
        cls.constructed = False
        cls.run_called = False


class _RecordingLogger:
    """Minimal logger that records structured log events."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


def test_lifespan_skips_dispatcher_when_env_unset(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY_PATH is not set, warn + skip."""
    monkeypatch.delenv("GITHUB_APP_ID", raising=False)
    monkeypatch.delenv("GITHUB_APP_PRIVATE_KEY_PATH", raising=False)

    recording_logger = _RecordingLogger()
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.structlog.get_logger",
        lambda: recording_logger,
    )

    _RecordingDispatcher.reset()
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Orchestrator",
        _RecordingDispatcher,
    )

    app = create_app(webhook_secret="test-secret")
    with TestClient(app):
        pass

    assert not _RecordingDispatcher.constructed
    warning_events = [e for e, _ in recording_logger.events if e == "app.dispatcher_skipped"]
    assert len(warning_events) == 1


def test_lifespan_skips_dispatcher_when_key_file_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When GITHUB_APP_PRIVATE_KEY_PATH points to a nonexistent file, warn + skip."""
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/does-not-exist-key.pem")

    recording_logger = _RecordingLogger()
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.structlog.get_logger",
        lambda: recording_logger,
    )

    _RecordingDispatcher.reset()
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Orchestrator",
        _RecordingDispatcher,
    )

    # Ensure the file truly doesn't exist (no real filesystem dependency)
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Path.exists",
        lambda self: False,
    )

    app = create_app(webhook_secret="test-secret")
    with TestClient(app):
        pass

    assert not _RecordingDispatcher.constructed
    warning_events = [
        (e, kw)
        for e, kw in recording_logger.events
        if e == "app.dispatcher_skipped"
    ]
    assert len(warning_events) == 1
    _, kw = warning_events[0]
    assert "does not exist" in kw["reason"]


def test_lifespan_starts_dispatcher_when_key_path_valid(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When key path is valid, load_private_key_pem is called and dispatcher starts."""
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/fake-key.pem")

    # File exists
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Path.exists",
        lambda self: True,
    )

    load_called_with: list[str] = []

    def fake_load_private_key_pem(path: str) -> str:
        load_called_with.append(str(path))
        return "-----BEGIN RSA PRIVATE KEY-----\nfake\n-----END RSA PRIVATE KEY-----"

    monkeypatch.setattr(
        "github_auto_maintainer.server.app.load_private_key_pem",
        fake_load_private_key_pem,
    )

    _RecordingDispatcher.reset()
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Orchestrator",
        _RecordingDispatcher,
    )

    recording_logger = _RecordingLogger()
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.structlog.get_logger",
        lambda: recording_logger,
    )

    app = create_app(webhook_secret="test-secret")
    with TestClient(app):
        pass

    assert load_called_with == ["/tmp/fake-key.pem"]
    assert _RecordingDispatcher.constructed


def test_lifespan_fails_when_key_file_unreadable(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When file exists but load_private_key_pem raises OSError, lifespan propagates."""
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/unreadable-key.pem")

    # File exists (so we don't hit the warn+skip path)
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Path.exists",
        lambda self: True,
    )

    def failing_load(path: str) -> str:
        raise OSError(f"Cannot read file: {path}")

    monkeypatch.setattr(
        "github_auto_maintainer.server.app.load_private_key_pem",
        failing_load,
    )

    app = create_app(webhook_secret="test-secret")
    with pytest.raises(OSError, match="Cannot read file"):
        with TestClient(app):
            pass
