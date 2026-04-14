"""End-to-end integration tests: queue → orchestrator → GitHub writes.

Tests create a real queue, real orchestrator with FakeProvider, and mock
all GitHub HTTP endpoints via respx.
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


# ── Fakes and helpers ─────────────────────────────────────────────────


class _FakeProvider(BaseLLMProvider):
    """Returns canned golden JSON for any completion call."""

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


def _make_router(golden_responses: list[str]) -> LLMRouter:
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
    provider = _FakeProvider(golden_responses)
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factory=lambda p, m, lm: provider,
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _private_key() -> str:
    return TEST_KEY.read_text(encoding="utf-8")


def _make_pr_event(
    *, delivery_id: str = "integ-delivery-pr-001",
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


def _make_issue_event(
    *, delivery_id: str = "integ-delivery-issue-001",
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


def _mock_installation_token() -> None:
    respx.post(f"{BASE}/app/installations/9876/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_test_token_123", "expires_at": "2026-04-08T12:00:00Z"},
        )
    )


def _pr_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "pr_opened_payload.json").read_text()
    )
    pr: dict[str, Any] = data["pull_request"]
    return pr


def _issue_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "issue_opened_payload.json").read_text()
    )
    issue: dict[str, Any] = data["issue"]
    return issue


def _small_diff() -> str:
    return (FIXTURES / "small_diff.patch").read_text()


def _make_orchestrator(
    *,
    golden_responses: list[str],
    policy: ActionPolicy | None = None,
    idempotency_store: InMemoryIdempotencyStore | None = None,
    skills: Sequence[Any] | None = None,
) -> Orchestrator:
    return Orchestrator(
        queue=InMemoryJobQueue[NormalizedEvent](),
        skills=skills or [PRSummarySkill(), IssueLabelSkill(), IssueResponseSkill()],
        router=_make_router(golden_responses),
        app_id="12345",
        private_key_pem=_private_key(),
        policy=policy or ActionPolicy(dry_run=False),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
        logger=structlog.get_logger(),
    )


# ── Integration tests ─────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_pr_opened_produces_review_summary() -> None:
    """Enqueue pull_request.opened → POST to reviews endpoint called once."""
    golden = (FIXTURES / "pr_summary_golden.json").read_text()
    orch = _make_orchestrator(golden_responses=[golden])

    _mock_installation_token()

    # Mock GET PR
    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        return_value=httpx.Response(200, json=_pr_json())
    )
    # Mock GET diff
    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        side_effect=[
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
        ]
    )
    # Mock POST review
    review_route = respx.post(f"{BASE}/repos/octocat/hello-world/pulls/42/reviews").mock(
        return_value=httpx.Response(
            201, json={"id": 1, "state": "COMMENTED", "body": "review"}
        )
    )

    event = _make_pr_event()
    await orch._process_event(event)

    assert review_route.call_count == 1
    # Verify the review body contains expected event type
    request_body: dict[str, Any] = json.loads(review_route.calls[0].request.content)
    assert request_body["event"] == "COMMENT"


@pytest.mark.asyncio
@respx.mock
async def test_issue_opened_produces_labels_and_comment() -> None:
    """Enqueue issues.opened → POST to labels AND POST to comments."""
    label_golden = (FIXTURES / "issue_label_golden.json").read_text()
    response_golden = (FIXTURES / "issue_response_golden.json").read_text()
    # Two skills will call the LLM — provide both golden responses
    orch = _make_orchestrator(golden_responses=[label_golden, response_golden])

    _mock_installation_token()

    # Mock GET issue (both skills read it)
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    # Mock GET comments (both skills read it)
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(200, json=[])
    )
    # Mock POST labels
    labels_route = respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/labels").mock(
        return_value=httpx.Response(
            200, json=[{"name": "bug"}, {"name": "high-priority"}, {"name": "login"}]
        )
    )
    # Mock POST comment
    comment_route = respx.post(
        f"{BASE}/repos/octocat/hello-world/issues/101/comments"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 1,
                "body": "response",
                "user": {"login": "bot"},
                "created_at": "2026-04-08T12:00:00Z",
                "updated_at": "2026-04-08T12:00:00Z",
            },
        )
    )

    event = _make_issue_event()
    await orch._process_event(event)

    assert labels_route.call_count == 1
    assert comment_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_duplicate_delivery_no_duplicate_write() -> None:
    """Process same delivery_id event twice → write endpoint called once."""
    golden = (FIXTURES / "pr_summary_golden.json").read_text()
    store = InMemoryIdempotencyStore()
    orch = _make_orchestrator(golden_responses=[golden, golden], idempotency_store=store)

    _mock_installation_token()

    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        side_effect=[
            # First call
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
            # Second call
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
        ]
    )
    review_route = respx.post(f"{BASE}/repos/octocat/hello-world/pulls/42/reviews").mock(
        return_value=httpx.Response(
            201, json={"id": 1, "state": "COMMENTED", "body": "review"}
        )
    )

    event = _make_pr_event()
    await orch._process_event(event)
    await orch._process_event(event)

    # Write endpoint called exactly once
    assert review_route.call_count == 1


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_no_writes() -> None:
    """Policy with dry_run=True → zero POST calls to GitHub write endpoints."""
    golden = (FIXTURES / "pr_summary_golden.json").read_text()
    policy = ActionPolicy(dry_run=True)
    orch = _make_orchestrator(golden_responses=[golden], policy=policy)

    _mock_installation_token()

    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        side_effect=[
            httpx.Response(200, json=_pr_json()),
            httpx.Response(200, text=_small_diff()),
        ]
    )
    review_route = respx.post(f"{BASE}/repos/octocat/hello-world/pulls/42/reviews").mock(
        return_value=httpx.Response(
            201, json={"id": 1, "state": "COMMENTED", "body": "review"}
        )
    )

    event = _make_pr_event()
    await orch._process_event(event)

    # Zero POST calls to write endpoints
    assert review_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_allowlist_rejection_no_skill_execution() -> None:
    """Policy with allowed_repositories=('other/repo',) → no GitHub API calls at all."""
    golden = (FIXTURES / "pr_summary_golden.json").read_text()
    policy = ActionPolicy(dry_run=False, allowed_repositories=("other/repo",))
    orch = _make_orchestrator(golden_responses=[golden], policy=policy)

    # No respx mocks registered — any HTTP call would raise

    event = _make_pr_event()
    await orch._process_event(event)

    # No calls at all
    assert respx.calls.call_count == 0
