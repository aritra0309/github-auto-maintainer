"""End-to-end integration test: webhook POST → queue → orchestrator → skill result.

Proves the full vertical slice is wired correctly:
1. POST /webhook returns 202
2. enqueue happened
3. dequeue happened
4. orchestrator.skill_executed was logged
5. logged decision has expected structure
"""

from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
from collections.abc import Sequence
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import httpx
import pytest

from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.task_types import TaskType
from github_auto_maintainer.github.auth import InstallationAccessToken
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.server.app import create_app

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
WEBHOOK_SECRET = "integration-test-secret"


# ── Test infrastructure ───────────────────────────────────────────────


class _FakeProvider(BaseLLMProvider):
    """Returns canned golden JSON for any completion call."""

    def __init__(self, golden_content: str) -> None:
        self._content = golden_content

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        _ = (system, messages, max_tokens, temperature)
        return LLMResponse(
            content=self._content,
            provider="fake",
            model="fake-model",
            input_tokens=100,
            output_tokens=50,
        )


def _make_router(golden_content: str) -> LLMRouter:
    catalog = ModelCatalog(
        models=(
            ModelDescriptor(
                provider="fake",
                model="fake-model",
                litellm_model="fake/fake-model",
                context_window=8000,
                cost_tier=1,
                suited_for=frozenset(
                    {TaskType.TRIAGE, TaskType.DEEP_REVIEW,
                     TaskType.SUMMARIZATION, TaskType.CLASSIFICATION}
                ),
            ),
        ),
    )
    provider = _FakeProvider(golden_content)
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factory=lambda p, m, lm: provider,
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


class _RecordingQueue:
    """Wraps InMemoryJobQueue with asyncio.Event signals for enqueue/dequeue."""

    def __init__(self) -> None:
        self._inner: InMemoryJobQueue[NormalizedEvent] = InMemoryJobQueue()
        self.enqueue_event = asyncio.Event()
        self.dequeue_event = asyncio.Event()

    async def enqueue(self, job: NormalizedEvent) -> None:
        await self._inner.enqueue(job)
        self.enqueue_event.set()

    async def dequeue(self) -> NormalizedEvent:
        item = await self._inner.dequeue()
        self.dequeue_event.set()
        return item

    def dequeue_nowait(self) -> NormalizedEvent:
        return self._inner.dequeue_nowait()

    def qsize(self) -> int:
        return self._inner.qsize()


class _RecordingLogger:
    """Captures structured log events for assertion."""

    def __init__(self) -> None:
        self.events: list[tuple[str, dict[str, Any]]] = []

    def info(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def warning(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def debug(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))

    def exception(self, event: str, **kw: Any) -> None:
        self.events.append((event, kw))


def _sign(body: bytes) -> str:
    digest = hmac.new(WEBHOOK_SECRET.encode(), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


# ── The integration test ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_webhook_to_skill_pr_triage(monkeypatch: pytest.MonkeyPatch) -> None:
    """Full vertical slice: webhook → queue → orchestrator → skill result log."""

    # 1. Set up environment for lifespan to start the orchestrator
    monkeypatch.setenv("GITHUB_APP_ID", "12345")
    monkeypatch.setenv("GITHUB_APP_PRIVATE_KEY_PATH", "/tmp/fake-key.pem")

    # 2. Monkeypatch Path.exists so the lifespan reaches load_private_key_pem
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.Path.exists",
        lambda self: True,
    )

    # 3. Monkeypatch auth: load_private_key_pem + JWT + installation token
    monkeypatch.setattr(
        "github_auto_maintainer.server.app.load_private_key_pem",
        lambda path: "fake-pem-key",
    )
    monkeypatch.setattr(
        "github_auto_maintainer.core.orchestrator.generate_github_app_jwt",
        lambda *, app_id, private_key_pem: "fake-jwt",
    )
    monkeypatch.setattr(
        "github_auto_maintainer.core.orchestrator.fetch_installation_access_token",
        AsyncMock(
            return_value=InstallationAccessToken(
                token="fake-token", expires_at="2026-12-31T00:00:00Z"
            )
        ),
    )

    # 4. Monkeypatch GitHubClient methods (avoid respx — client has its own tests)
    pr_data = json.loads((FIXTURES / "pr_opened_payload.json").read_text())["pull_request"]
    small_diff = (FIXTURES / "small_diff.patch").read_text()

    async def mock_get_pr(self: Any, owner: str, repo: str, number: int) -> Any:
        from github_auto_maintainer.github.client import _parse_pull_request

        return _parse_pull_request(pr_data)

    async def mock_get_diff(self: Any, owner: str, repo: str, number: int) -> str:
        return small_diff

    # Mock write methods to no-op (orchestrator will try to write)
    async def mock_create_review(
        self: Any, owner: str, repo: str, pr_number: int, body: str, **kw: Any
    ) -> Any:
        from github_auto_maintainer.github.client import PullRequestReview

        return PullRequestReview(id=1, state="COMMENTED", body=body)

    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.get_pull_request",
        mock_get_pr,
    )
    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.get_pull_request_diff",
        mock_get_diff,
    )
    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.create_pr_review_summary",
        mock_create_review,
    )

    # 5. Build recording infrastructure
    recording_queue = _RecordingQueue()
    recording_logger = _RecordingLogger()
    golden_json = (FIXTURES / "pr_summary_golden.json").read_text()
    router = _make_router(golden_json)

    monkeypatch.setattr(
        "github_auto_maintainer.server.app.structlog.get_logger",
        lambda *_args, **_kwargs: recording_logger,
    )

    # 6. Create the app with injectable dependencies
    app = create_app(
        queue=recording_queue,
        webhook_secret=WEBHOOK_SECRET,
        router=router,
    )

    # 7. Enter lifespan explicitly, then use httpx.ASGITransport for requests
    async with app.router.lifespan_context(app):
        transport = httpx.ASGITransport(app=app)
        async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
            # Build and sign the webhook payload
            payload = json.loads((FIXTURES / "pr_opened_payload.json").read_text())
            body = json.dumps(payload).encode("utf-8")
            signature = _sign(body)

            # POST /webhook
            response = await client.post(
                "/webhook",
                content=body,
                headers={
                    "X-GitHub-Event": "pull_request",
                    "X-GitHub-Delivery": "integration-test-delivery-001",
                    "X-Hub-Signature-256": signature,
                    "Content-Type": "application/json",
                },
            )

            # ── Assertion 1: POST returns 202 ────────────────────────
            assert response.status_code == 202
            assert response.json() == {"status": "accepted"}

            # ── Assertion 2: enqueue happened ─────────────────────────
            await asyncio.wait_for(recording_queue.enqueue_event.wait(), timeout=5.0)
            assert recording_queue.enqueue_event.is_set()

            # ── Assertion 3: dequeue happened ─────────────────────────
            await asyncio.wait_for(recording_queue.dequeue_event.wait(), timeout=5.0)
            assert recording_queue.dequeue_event.is_set()

            # ── Assertion 4: orchestrator.skill_executed log was emitted
            # Poll for the log event (orchestrator runs as a background task)
            deadline = asyncio.get_event_loop().time() + 5.0
            skill_result_events: list[tuple[str, dict[str, Any]]] = []
            while asyncio.get_event_loop().time() < deadline:
                skill_result_events = [
                    (event, kw)
                    for event, kw in recording_logger.events
                    if event == "orchestrator.skill_executed"
                ]
                if skill_result_events:
                    break
                await asyncio.sleep(0.05)

            assert len(skill_result_events) >= 1, (
                f"Expected orchestrator.skill_executed log, got events: "
                f"{[e for e, _ in recording_logger.events]}"
            )

            # ── Assertion 5: decision has expected structure ──────────
            _, result_kw = skill_result_events[0]
            assert result_kw["skill_name"] == "pr_summary"
            assert result_kw["delivery_id"] == "integration-test-delivery-001"
            assert result_kw["event_type"] == "pull_request.opened"
            assert result_kw["repository"] == "octocat/hello-world"
            assert isinstance(result_kw["model"], str)
            assert isinstance(result_kw["elapsed_seconds"], float)
            assert "planned_actions_count" in result_kw

            decision = result_kw["decision"]
            assert isinstance(decision, dict)
            expected_keys = {
                "summary", "key_changes", "suggestions", "risk_level",
            }
            assert expected_keys.issubset(decision.keys()), (
                f"Decision missing keys: {expected_keys - decision.keys()}"
            )
            assert decision["risk_level"] == "medium"
