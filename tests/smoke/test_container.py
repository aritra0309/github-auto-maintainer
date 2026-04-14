"""Container smoke tests — verify the Docker image builds and serves correctly.

Marked with ``@pytest.mark.smoke`` so they can be run separately from the
unit/integration suite::

    pytest tests/smoke/ -m smoke

These tests require Docker to be available and will build the image from the
repository root.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import subprocess
import time
from typing import Any

import httpx
import pytest

IMAGE_NAME = "github-auto-maintainer-smoke"
CONTAINER_NAME = "gham-smoke-test"
HOST_PORT = 18321  # Unlikely to collide with anything
WEBHOOK_SECRET = "smoke-test-secret-42"


def _docker_available() -> bool:
    """Return True if the Docker CLI is reachable."""
    try:
        result = subprocess.run(
            ["docker", "info"],
            capture_output=True,
            timeout=10,
        )
        return result.returncode == 0
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


pytestmark = [
    pytest.mark.smoke,
    pytest.mark.skipif(not _docker_available(), reason="Docker not available"),
]


def _sign_payload(secret: str, body: bytes) -> str:
    """Compute the ``X-Hub-Signature-256`` value for *body*."""
    digest = hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


@pytest.fixture(scope="module")
def container() -> Any:
    """Build the image and start a container for the module's tests.

    Yields once the health endpoint responds, tears down on exit.
    """
    # 1. Build image
    build = subprocess.run(
        ["docker", "build", "-t", IMAGE_NAME, "."],
        capture_output=True,
        text=True,
        timeout=300,
    )
    if build.returncode != 0:
        pytest.fail(f"Docker build failed:\n{build.stderr}")

    # 2. Start container — no GitHub App keys, so the orchestrator won't
    #    start, but the health and webhook endpoints will still be available.
    subprocess.run(
        ["docker", "rm", "-f", CONTAINER_NAME],
        capture_output=True,
    )
    run = subprocess.run(
        [
            "docker", "run", "-d",
            "--name", CONTAINER_NAME,
            "-p", f"{HOST_PORT}:8000",
            "-e", f"GITHUB_WEBHOOK_SECRET={WEBHOOK_SECRET}",
            "-e", "LOG_FORMAT=json",
            IMAGE_NAME,
            "github-maintainer", "serve",
        ],
        capture_output=True,
        text=True,
        timeout=30,
    )
    if run.returncode != 0:
        pytest.fail(f"Docker run failed:\n{run.stderr}")

    _ = run.stdout.strip()  # container ID (used for debugging)

    # 3. Wait for health endpoint (up to 30 s).
    base_url = f"http://localhost:{HOST_PORT}"
    deadline = time.monotonic() + 30
    healthy = False
    while time.monotonic() < deadline:
        try:
            resp = httpx.get(f"{base_url}/health", timeout=2)
            if resp.status_code == 200:
                healthy = True
                break
        except httpx.ConnectError:
            pass
        time.sleep(1)

    if not healthy:
        logs = subprocess.run(
            ["docker", "logs", CONTAINER_NAME],
            capture_output=True,
            text=True,
        )
        subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)
        pytest.fail(f"Container never became healthy.\nLogs:\n{logs.stdout}\n{logs.stderr}")

    yield base_url

    # 4. Tear down
    subprocess.run(["docker", "rm", "-f", CONTAINER_NAME], capture_output=True)


def test_health_endpoint(container: str) -> None:
    """GET /health returns 200 with ``{"status": "ok"}``."""
    resp = httpx.get(f"{container}/health", timeout=5)
    assert resp.status_code == 200
    assert resp.json() == {"status": "ok"}


def test_valid_webhook_accepted(container: str) -> None:
    """POST /webhook with a valid signature returns 202."""
    payload: dict[str, Any] = {
        "action": "opened",
        "repository": {
            "id": 1,
            "full_name": "octocat/hello-world",
            "name": "hello-world",
            "owner": {"login": "octocat"},
        },
        "sender": {"login": "octocat"},
        "installation": {"id": 9876},
        "issue": {
            "number": 1,
            "title": "Test issue",
            "body": "Smoke test",
            "user": {"login": "octocat"},
            "labels": [],
            "state": "open",
            "created_at": "2026-04-14T00:00:00Z",
            "updated_at": "2026-04-14T00:00:00Z",
        },
    }
    body = json.dumps(payload).encode()
    signature = _sign_payload(WEBHOOK_SECRET, body)

    resp = httpx.post(
        f"{container}/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "smoke-delivery-001",
            "X-Hub-Signature-256": signature,
        },
        timeout=5,
    )
    assert resp.status_code == 202
    assert resp.json() == {"status": "accepted"}


def test_invalid_signature_rejected(container: str) -> None:
    """POST /webhook with a bad signature returns 401."""
    body = b'{"action": "opened"}'
    bad_sig = "sha256=" + "a" * 64

    resp = httpx.post(
        f"{container}/webhook",
        content=body,
        headers={
            "Content-Type": "application/json",
            "X-GitHub-Event": "issues",
            "X-GitHub-Delivery": "smoke-delivery-bad",
            "X-Hub-Signature-256": bad_sig,
        },
        timeout=5,
    )
    assert resp.status_code == 401


def test_missing_headers_rejected(container: str) -> None:
    """POST /webhook without required headers returns 400."""
    resp = httpx.post(
        f"{container}/webhook",
        content=b'{"action": "opened"}',
        headers={"Content-Type": "application/json"},
        timeout=5,
    )
    assert resp.status_code == 400
