"""Resilience tests — verify graceful degradation under adverse conditions.

These tests exercise the orchestrator's error handling paths:
- Duplicate webhook delivery → idempotency prevents double-writes
- LLM provider outage → skill error is logged, other skills continue
- GitHub API rate limiting → action fails gracefully, remaining actions proceed
"""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import structlog

from github_auto_maintainer.core.action_policy import ActionPolicy
from github_auto_maintainer.core.errors import TransientProviderError
from github_auto_maintainer.core.idempotency import InMemoryIdempotencyStore
from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.orchestrator import Orchestrator
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.task_types import TaskType
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.skills.issue_label import IssueLabelSkill
from github_auto_maintainer.skills.issue_response import IssueResponseSkill
from github_auto_maintainer.skills.pr_summary import PRSummarySkill

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BASE = "https://api.github.com"
TEST_KEY = FIXTURES / "github_app_test_private_key.pem"


# ── Fakes ──────────────────────────────────────────────────────────────────


class _FakeProvider(BaseLLMProvider):
    """Returns canned JSON for completion calls."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = list(responses)
        self._call_idx = 0

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        _ = (system, messages, max_tokens, temperature)
        content = self._responses[min(self._call_idx, len(self._responses) - 1)]
        self._call_idx += 1
        return LLMResponse(
            content=content,
            provider="fake",
            model="fake-model",
            input_tokens=100,
            output_tokens=50,
        )


class _ExplodingProvider(BaseLLMProvider):
    """Raises TransientProviderError on every call."""

    async def complete(
        self,
        system: str,
        messages: Sequence[LLMMessage],
        max_tokens: int,
        temperature: float,
    ) -> LLMResponse:
        _ = (system, messages, max_tokens, temperature)
        raise TransientProviderError("Provider is down — simulated outage")


# ── Helpers ────────────────────────────────────────────────────────────────


def _make_catalog() -> ModelCatalog:
    return ModelCatalog(
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


def _make_router(provider: BaseLLMProvider) -> LLMRouter:
    catalog = _make_catalog()
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factory=lambda p, m, lm: provider,
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _private_key() -> str:
    return TEST_KEY.read_text(encoding="utf-8")


def _make_issue_event(
    *, delivery_id: str = "chaos-delivery-001",
) -> NormalizedEvent:
    payload: dict[str, Any] = json.loads(
        (FIXTURES / "issue_opened_payload.json").read_text()
    )
    return NormalizedEvent(
        event_name="issues.opened",
        delivery_id=delivery_id,
        github_event="issues",
        action="opened",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload=payload,
    )


def _make_pr_event(
    *, delivery_id: str = "chaos-delivery-pr-001",
) -> NormalizedEvent:
    payload: dict[str, Any] = json.loads(
        (FIXTURES / "pr_opened_payload.json").read_text()
    )
    return NormalizedEvent(
        event_name="pull_request.opened",
        delivery_id=delivery_id,
        github_event="pull_request",
        action="opened",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload=payload,
    )


def _mock_installation_token() -> None:
    respx.post(f"{BASE}/app/installations/9876/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_test_token_123", "expires_at": "2026-04-08T12:00:00Z"},
        )
    )


def _issue_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "issue_opened_payload.json").read_text()
    )
    issue: dict[str, Any] = data["issue"]
    return issue


def _pr_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "pr_opened_payload.json").read_text()
    )
    pr: dict[str, Any] = data["pull_request"]
    return pr


def _small_diff() -> str:
    return (FIXTURES / "small_diff.patch").read_text()


def _make_orchestrator(
    *,
    provider: BaseLLMProvider,
    policy: ActionPolicy | None = None,
    idempotency_store: InMemoryIdempotencyStore | None = None,
    skills: Sequence[Any] | None = None,
) -> Orchestrator:
    return Orchestrator(
        queue=InMemoryJobQueue[NormalizedEvent](),
        skills=skills or [PRSummarySkill(), IssueLabelSkill(), IssueResponseSkill()],
        router=_make_router(provider),
        app_id="12345",
        private_key_pem=_private_key(),
        policy=policy or ActionPolicy(dry_run=False),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
        logger=structlog.get_logger(),
    )


# ── Tests ──────────────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_duplicate_event_replay_idempotent() -> None:
    """Same delivery_id processed twice → write endpoints called exactly once.

    This verifies the idempotency layer correctly deduplicates actions
    when the same webhook delivery is replayed (e.g. GitHub retry).
    """
    golden = (FIXTURES / "pr_summary_golden.json").read_text()
    store = InMemoryIdempotencyStore()
    provider = _FakeProvider([golden, golden])
    orch = _make_orchestrator(
        provider=provider,
        idempotency_store=store,
        skills=[PRSummarySkill()],
    )

    _mock_installation_token()

    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        side_effect=[
            # First processing
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
            # Second processing
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
        ]
    )
    review_route = respx.post(
        f"{BASE}/repos/octocat/hello-world/pulls/42/reviews"
    ).mock(
        return_value=httpx.Response(
            201, json={"id": 1, "state": "COMMENTED", "body": "review"}
        )
    )

    event = _make_pr_event()
    await orch._process_event(event)
    await orch._process_event(event)

    # Write endpoint called exactly once despite two processing passes
    assert review_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_provider_outage_logged_gracefully() -> None:
    """LLM provider raises TransientProviderError → orchestrator logs and continues.

    The orchestrator must not crash. The skill execution fails, but the
    event processing loop stays alive.
    """
    provider = _ExplodingProvider()
    orch = _make_orchestrator(
        provider=provider,
        skills=[IssueLabelSkill(), IssueResponseSkill()],
    )

    _mock_installation_token()

    # Mock GET issue + comments (skills will try to read before calling LLM)
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    # No write mocks — the LLM call should fail before any writes happen
    event = _make_issue_event()

    # Should NOT raise — errors are caught and logged
    await orch._process_event(event)


@pytest.mark.asyncio
@respx.mock
async def test_github_rate_limit_action_fails_gracefully() -> None:
    """GitHub API returns 403 rate-limit → action fails but doesn't crash.

    The orchestrator catches GitHubClientError on action execution and
    logs the failure without re-raising.
    """
    golden = (FIXTURES / "issue_label_golden.json").read_text()
    response_golden = (FIXTURES / "issue_response_golden.json").read_text()
    provider = _FakeProvider([golden, response_golden])
    orch = _make_orchestrator(
        provider=provider,
        skills=[IssueLabelSkill(), IssueResponseSkill()],
    )

    _mock_installation_token()

    # Mock GET issue + comments
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    # Mock POST labels → 403 rate-limited
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/labels").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1999999999",
            },
        )
    )

    # Mock POST comment → also 403 rate-limited
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(
            403,
            json={"message": "API rate limit exceeded"},
            headers={
                "X-RateLimit-Remaining": "0",
                "X-RateLimit-Reset": "1999999999",
            },
        )
    )

    event = _make_issue_event()

    # Should NOT raise — action failures are caught and logged
    await orch._process_event(event)


@pytest.mark.asyncio
@respx.mock
async def test_github_transient_error_does_not_crash_orchestrator() -> None:
    """GitHub API returns 502 → action fails gracefully.

    Even though the retry decorator on the client may retry a few times,
    if it ultimately fails, the orchestrator catches the error.
    """
    golden = (FIXTURES / "pr_summary_golden.json").read_text()
    provider = _FakeProvider([golden])
    orch = _make_orchestrator(
        provider=provider,
        skills=[PRSummarySkill()],
    )

    _mock_installation_token()

    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        side_effect=[
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
        ]
    )

    # Mock POST review → 502 Bad Gateway (transient)
    respx.post(f"{BASE}/repos/octocat/hello-world/pulls/42/reviews").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )

    event = _make_pr_event()

    # Should NOT raise — transient errors are caught
    await orch._process_event(event)
