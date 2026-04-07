from __future__ import annotations

import asyncio
import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from unittest.mock import AsyncMock

import pytest
import structlog

from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.skill_dispatcher import SkillDispatcher
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.auth import InstallationAccessToken
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.skills.issue_triage import IssueTriageSkill
from github_auto_maintainer.skills.pr_triage import PRTriageSkill

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def _pr_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "pr_opened_payload.json").read_text())
    return data


def _issue_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads((FIXTURES / "issue_opened_payload.json").read_text())
    return data


def _golden_pr_json() -> str:
    return (FIXTURES / "pr_triage_golden.json").read_text()


def _golden_issue_json() -> str:
    return (FIXTURES / "issue_triage_golden.json").read_text()


class FakeProvider(BaseLLMProvider):
    """Provider that returns different content based on call order."""

    def __init__(self, responses: list[str]) -> None:
        self._responses = responses
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
            input_tokens=50,
            output_tokens=25,
        )


def _make_router(responses: list[str]) -> LLMRouter:
    catalog = ModelCatalog(
        models=(
            ModelDescriptor(
                provider="fake",
                model="fake-model",
                context_window=8000,
                cost_tier=TaskComplexity.LOW,
                suited_for=frozenset({TaskType.TRIAGE, TaskType.DEEP_REVIEW}),
            ),
        ),
        source_path=Path("/tmp/test-catalog.yaml"),
    )
    provider = FakeProvider(responses)
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factories={"fake": lambda model: provider},
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _make_event(
    event_name: str,
    payload: dict[str, Any],
    installation_id: int | None = 9876,
) -> NormalizedEvent:
    github_event = event_name.split(".")[0]
    action = event_name.split(".")[1] if "." in event_name else "unknown"
    return NormalizedEvent(
        event_name=event_name,
        delivery_id=f"delivery-{event_name}",
        github_event=github_event,
        action=action,
        installation_id=installation_id,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload=payload,
    )


@pytest.mark.asyncio
async def test_dispatcher_skips_unmatched_events() -> None:
    """Events that no skill handles should be logged and skipped."""
    queue: InMemoryJobQueue[NormalizedEvent] = InMemoryJobQueue()
    router = _make_router([_golden_pr_json()])
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    dispatcher = SkillDispatcher(
        queue=queue,
        skills=[PRTriageSkill(), IssueTriageSkill()],
        router=router,
        app_id="12345",
        private_key_pem="fake-key",
        logger=logger,
    )

    # push_event is an event type no skill handles
    event = _make_event("push.unknown", {"ref": "refs/heads/main"})
    await queue.enqueue(event)

    task = asyncio.create_task(dispatcher.run())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    # No error should have been raised — just logged and skipped
    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_dispatcher_skips_events_without_installation_id() -> None:
    """Events without installation_id should be logged and skipped."""
    queue: InMemoryJobQueue[NormalizedEvent] = InMemoryJobQueue()
    router = _make_router([_golden_pr_json()])
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    dispatcher = SkillDispatcher(
        queue=queue,
        skills=[PRTriageSkill()],
        router=router,
        app_id="12345",
        private_key_pem="fake-key",
        logger=logger,
    )

    event = _make_event("pull_request.opened", _pr_payload(), installation_id=None)
    await queue.enqueue(event)

    task = asyncio.create_task(dispatcher.run())
    await asyncio.sleep(0.1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_dispatcher_processes_pr_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher should match PR events to PRTriageSkill and execute."""
    queue: InMemoryJobQueue[NormalizedEvent] = InMemoryJobQueue()
    golden = _golden_pr_json()
    router = _make_router([golden])
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    # Mock auth functions
    monkeypatch.setattr(
        "github_auto_maintainer.core.skill_dispatcher.generate_github_app_jwt",
        lambda *, app_id, private_key_pem: "fake-jwt",
    )
    monkeypatch.setattr(
        "github_auto_maintainer.core.skill_dispatcher.fetch_installation_access_token",
        AsyncMock(return_value=InstallationAccessToken(token="fake-token", expires_at="2026-12-31T00:00:00Z")),
    )

    # Mock the GitHubClient methods
    small_diff = (FIXTURES / "small_diff.patch").read_text()
    pr_data = _pr_payload()["pull_request"]

    async def mock_get_pr(self: Any, owner: str, repo: str, number: int) -> Any:
        from github_auto_maintainer.github.client import _parse_pull_request
        return _parse_pull_request(pr_data)

    async def mock_get_diff(self: Any, owner: str, repo: str, number: int) -> str:
        return small_diff

    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.get_pull_request",
        mock_get_pr,
    )
    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.get_pull_request_diff",
        mock_get_diff,
    )

    dispatcher = SkillDispatcher(
        queue=queue,
        skills=[PRTriageSkill(), IssueTriageSkill()],
        router=router,
        app_id="12345",
        private_key_pem="fake-key",
        logger=logger,
    )

    event = _make_event("pull_request.opened", _pr_payload())
    await queue.enqueue(event)

    task = asyncio.create_task(dispatcher.run())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert queue.qsize() == 0


@pytest.mark.asyncio
async def test_dispatcher_processes_issue_event(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Dispatcher should match issue events to IssueTriageSkill and execute."""
    queue: InMemoryJobQueue[NormalizedEvent] = InMemoryJobQueue()
    golden = _golden_issue_json()
    router = _make_router([golden])
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    monkeypatch.setattr(
        "github_auto_maintainer.core.skill_dispatcher.generate_github_app_jwt",
        lambda *, app_id, private_key_pem: "fake-jwt",
    )
    monkeypatch.setattr(
        "github_auto_maintainer.core.skill_dispatcher.fetch_installation_access_token",
        AsyncMock(return_value=InstallationAccessToken(token="fake-token", expires_at="2026-12-31T00:00:00Z")),
    )

    issue_data = _issue_payload()["issue"]

    async def mock_get_issue(self: Any, owner: str, repo: str, number: int) -> Any:
        from github_auto_maintainer.github.client import _parse_issue
        return _parse_issue(issue_data)

    async def mock_get_comments(self: Any, owner: str, repo: str, number: int) -> tuple[()]:
        return ()

    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.get_issue",
        mock_get_issue,
    )
    monkeypatch.setattr(
        "github_auto_maintainer.github.client.GitHubClient.get_issue_comments",
        mock_get_comments,
    )

    dispatcher = SkillDispatcher(
        queue=queue,
        skills=[PRTriageSkill(), IssueTriageSkill()],
        router=router,
        app_id="12345",
        private_key_pem="fake-key",
        logger=logger,
    )

    event = _make_event("issues.opened", _issue_payload())
    await queue.enqueue(event)

    task = asyncio.create_task(dispatcher.run())
    await asyncio.sleep(0.3)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert queue.qsize() == 0
