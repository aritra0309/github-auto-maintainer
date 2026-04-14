from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
import structlog

from github_auto_maintainer.core.actions import IssueCommentAction
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.skills.base import SkillContext
from github_auto_maintainer.skills.issue_response import IssueResponseSkill

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BASE = "https://api.github.com"


def _golden_issue_response_json() -> str:
    return (FIXTURES / "issue_response_golden.json").read_text()


def _issue_payload() -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / "issue_opened_payload.json").read_text())
    return data


class FakeProvider(BaseLLMProvider):
    def __init__(self, response_content: str) -> None:
        self._content = response_content

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
            input_tokens=80,
            output_tokens=40,
        )


def _make_router(response_content: str) -> LLMRouter:
    catalog = ModelCatalog(
        models=(
            ModelDescriptor(
                provider="fake",
                model="fake-model",
                litellm_model="fake/fake-model",
                context_window=8000,
                cost_tier=1,
                suited_for=frozenset({TaskType.SUMMARIZATION}),
            ),
        ),
    )
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factory=lambda p, m, lm: FakeProvider(response_content),
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _make_event(payload: dict[str, object] | None = None) -> NormalizedEvent:
    p = payload or _issue_payload()
    return NormalizedEvent(
        event_name="issues.opened",
        delivery_id="test-delivery-issue-response",
        github_event="issues",
        action="opened",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload=p,
    )


# ── Unit tests ────────────────────────────────────────────────────────


def test_handles_event_correct() -> None:
    skill = IssueResponseSkill()
    event = _make_event()
    assert skill.handles_event(event) is True


def test_does_not_handle_wrong_event() -> None:
    skill = IssueResponseSkill()
    event = NormalizedEvent(
        event_name="pull_request.opened",
        delivery_id="d-1",
        github_event="pull_request",
        action="opened",
        installation_id=1,
        repository_full_name="o/r",
        repository_id=1,
        received_at=datetime.now(tz=UTC),
        payload={},
    )
    assert skill.handles_event(event) is False


def test_skill_properties() -> None:
    skill = IssueResponseSkill()
    assert skill.name == "issue_response"
    assert skill.default_task_type == TaskType.SUMMARIZATION
    assert skill.default_complexity == TaskComplexity.LOW


# ── Integration test ──────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_end_to_end() -> None:
    golden = _golden_issue_response_json()
    router = _make_router(golden)
    event = _make_event()

    issue_json = _issue_payload()["issue"]
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=issue_json)
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(200, json=[])
    )

    skill = IssueResponseSkill()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=logger,
        )
        result = await skill.execute(context)

    assert result.skill_name == "issue_response"
    assert result.event_delivery_id == "test-delivery-issue-response"

    # Decision fields match golden fixture
    assert result.decision.response_body.startswith("Thank you for reporting")
    assert result.decision.needs_more_info is True
    assert result.decision.category == "bug_report"

    # Planned actions
    assert len(result.planned_actions) == 1
    action = result.planned_actions[0]
    assert isinstance(action, IssueCommentAction)
    assert action.owner == "octocat"
    assert action.repo == "hello-world"
    assert action.issue_number == 101
    assert action.body.startswith("Thank you for reporting")

    # Recommended actions populated
    assert len(result.recommended_actions) > 0

    assert result.model_used == "fake-model"
    assert result.task_type_used == TaskType.SUMMARIZATION
    assert result.complexity_used == TaskComplexity.LOW
    assert result.elapsed_seconds >= 0
