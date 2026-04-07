from __future__ import annotations

import hashlib
import hmac
import json

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
