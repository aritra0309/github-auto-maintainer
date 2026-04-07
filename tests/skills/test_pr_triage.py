from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path

import httpx
import pytest
import respx
import structlog

from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.skills.base import SkillContext
from github_auto_maintainer.skills.pr_triage import PRTriageSkill, _compute_routing

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BASE = "https://api.github.com"


def _golden_pr_json() -> str:
    return (FIXTURES / "pr_triage_golden.json").read_text()


def _pr_payload() -> dict[str, object]:
    data: dict[str, object] = json.loads((FIXTURES / "pr_opened_payload.json").read_text())
    return data


def _small_diff() -> str:
    return (FIXTURES / "small_diff.patch").read_text()


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
            input_tokens=100,
            output_tokens=50,
        )


def _make_router(response_content: str) -> LLMRouter:
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
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factories={"fake": lambda model: FakeProvider(response_content)},
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _make_event(payload: dict[str, object] | None = None) -> NormalizedEvent:
    p = payload or _pr_payload()
    return NormalizedEvent(
        event_name="pull_request.opened",
        delivery_id="test-delivery-123",
        github_event="pull_request",
        action="opened",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload=p,
    )


# ── Unit tests ────────────────────────────────────────────────────────


def test_handles_event_pr_opened() -> None:
    skill = PRTriageSkill()
    event = _make_event()
    assert skill.handles_event(event) is True


def test_handles_event_pr_synchronize() -> None:
    skill = PRTriageSkill()
    payload = _pr_payload()
    event = NormalizedEvent(
        event_name="pull_request.synchronize",
        delivery_id="d-1",
        github_event="pull_request",
        action="synchronize",
        installation_id=1,
        repository_full_name="o/r",
        repository_id=1,
        received_at=datetime.now(tz=UTC),
        payload=payload,
    )
    assert skill.handles_event(event) is True


def test_does_not_handle_issues() -> None:
    skill = PRTriageSkill()
    event = NormalizedEvent(
        event_name="issues.opened",
        delivery_id="d-1",
        github_event="issues",
        action="opened",
        installation_id=1,
        repository_full_name="o/r",
        repository_id=1,
        received_at=datetime.now(tz=UTC),
        payload={},
    )
    assert skill.handles_event(event) is False


def test_skill_properties() -> None:
    skill = PRTriageSkill()
    assert skill.name == "pr_triage"
    assert skill.default_task_type == TaskType.TRIAGE
    assert skill.default_complexity == TaskComplexity.LOW


def test_compute_routing_small() -> None:
    task_type, complexity = _compute_routing(total_changed=30, total_files=2)
    assert task_type == TaskType.TRIAGE
    assert complexity == TaskComplexity.LOW


def test_compute_routing_medium() -> None:
    task_type, complexity = _compute_routing(total_changed=150, total_files=5)
    assert task_type == TaskType.DEEP_REVIEW
    assert complexity == TaskComplexity.MEDIUM


def test_compute_routing_large_by_lines() -> None:
    task_type, complexity = _compute_routing(total_changed=500, total_files=5)
    assert task_type == TaskType.DEEP_REVIEW
    assert complexity == TaskComplexity.HIGH


def test_compute_routing_large_by_files() -> None:
    task_type, complexity = _compute_routing(total_changed=100, total_files=15)
    assert task_type == TaskType.DEEP_REVIEW
    assert complexity == TaskComplexity.HIGH


# ── Integration test ──────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_pr_triage_execute_end_to_end() -> None:
    golden = _golden_pr_json()
    router = _make_router(golden)
    event = _make_event()

    pr_json = _pr_payload()["pull_request"]
    respx.get(f"{BASE}/repos/octocat/hello-world/pulls/42").mock(
        side_effect=[
            httpx.Response(200, json=pr_json),
            httpx.Response(200, text=_small_diff()),
        ]
    )

    skill = PRTriageSkill()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger()

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=logger,
        )
        result = await skill.execute(context)

    assert result.skill_name == "pr_triage"
    assert result.event_delivery_id == "test-delivery-123"
    assert result.decision.priority == "medium"
    assert result.decision.category == "bug_fix"
    assert result.model_used == "fake-model"
    assert result.elapsed_seconds >= 0
