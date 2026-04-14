"""Tests for core/orchestrator.py — the Phase 4 event-to-action orchestrator."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx
import pytest
import respx
import structlog

from github_auto_maintainer.core.action_policy import ActionPolicy
from github_auto_maintainer.core.actions import (
    AddLabelsAction,
    IssueCommentAction,
)
from github_auto_maintainer.core.idempotency import InMemoryIdempotencyStore
from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.orchestrator import Orchestrator
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BASE = "https://api.github.com"
TEST_KEY = FIXTURES / "github_app_test_private_key.pem"


# ── Fakes ──────────────────────────────────────────────────────────────


class FakeProvider(BaseLLMProvider):
    """Returns canned LLM responses for testing."""

    def __init__(self, response_content: str = "{}") -> None:
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


class FakeSkill(BaseSkill):
    """Configurable fake skill for orchestrator testing."""

    def __init__(
        self,
        *,
        skill_name: str = "fake_skill",
        handled_events: frozenset[str] = frozenset({"issues.opened"}),
        planned_actions: tuple[Any, ...] = (),
        should_raise: Exception | None = None,
    ) -> None:
        self._name = skill_name
        self._handled_events = handled_events
        self._planned_actions = planned_actions
        self._should_raise = should_raise
        self.execute_count = 0

    @property
    def name(self) -> str:
        return self._name

    @property
    def description(self) -> str:
        return "Fake skill for testing."

    @property
    def default_task_type(self) -> TaskType:
        return TaskType.TRIAGE

    @property
    def default_complexity(self) -> TaskComplexity:
        return TaskComplexity.LOW

    def handles_event(self, event: NormalizedEvent) -> bool:
        return event.event_name in self._handled_events

    async def execute(self, context: SkillContext) -> SkillResult[str]:
        self.execute_count += 1
        if self._should_raise is not None:
            raise self._should_raise
        return SkillResult(
            skill_name=self._name,
            event_delivery_id=context.event.delivery_id,
            decision="fake-decision",
            confidence=0.9,
            reasoning="test reasoning",
            recommended_actions=("test_action",),
            model_used="fake-model",
            task_type_used=TaskType.TRIAGE,
            complexity_used=TaskComplexity.LOW,
            elapsed_seconds=0.01,
            planned_actions=self._planned_actions,
        )


# ── Helpers ────────────────────────────────────────────────────────────


def _make_router() -> LLMRouter:
    catalog = ModelCatalog(
        models=(
            ModelDescriptor(
                provider="fake",
                model="fake-model",
                litellm_model="fake/fake-model",
                context_window=8000,
                cost_tier=1,
                suited_for=frozenset({TaskType.TRIAGE}),
            ),
        ),
    )
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factory=lambda p, m, lm: FakeProvider(),
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _make_event(
    *,
    event_name: str = "issues.opened",
    delivery_id: str = "delivery-001",
    repository_full_name: str = "octocat/hello-world",
    installation_id: int | None = 9876,
) -> NormalizedEvent:
    return NormalizedEvent(
        event_name=event_name,
        delivery_id=delivery_id,
        github_event=event_name.split(".")[0],
        action=event_name.split(".")[-1] if "." in event_name else "unknown",
        installation_id=installation_id,
        repository_full_name=repository_full_name,
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload={
            "action": "opened",
            "repository": {
                "id": 12345,
                "name": "hello-world",
                "full_name": repository_full_name,
                "owner": {"login": "octocat"},
            },
            "installation": {"id": installation_id},
            "sender": {"login": "contributor1"},
        },
    )


def _private_key() -> str:
    return TEST_KEY.read_text(encoding="utf-8")


def _mock_installation_token() -> None:
    """Register a respx mock for the installation token endpoint."""
    respx.post(f"{BASE}/app/installations/9876/access_tokens").mock(
        return_value=httpx.Response(
            201,
            json={"token": "ghs_test_token_123", "expires_at": "2026-04-08T12:00:00Z"},
        )
    )


def _make_orchestrator(
    *,
    skills: Sequence[BaseSkill],
    policy: ActionPolicy | None = None,
    idempotency_store: InMemoryIdempotencyStore | None = None,
) -> Orchestrator:
    return Orchestrator(
        queue=InMemoryJobQueue[NormalizedEvent](),
        skills=skills,
        router=_make_router(),
        app_id="12345",
        private_key_pem=_private_key(),
        policy=policy or ActionPolicy(dry_run=False),
        idempotency_store=idempotency_store or InMemoryIdempotencyStore(),
        logger=structlog.get_logger(),
    )


# ── Test cases ─────────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_repo_not_in_allowlist_no_skills_executed() -> None:
    """1. Repo not in allowlist → no skills executed, no GitHub calls."""
    skill = FakeSkill()
    policy = ActionPolicy(
        dry_run=False,
        allowed_repositories=("other/repo",),
    )
    orch = _make_orchestrator(skills=[skill], policy=policy)
    event = _make_event(repository_full_name="octocat/hello-world")

    await orch._process_event(event)

    assert skill.execute_count == 0
    assert respx.calls.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_event_not_in_allowlist_no_skills_executed() -> None:
    """2. Event not in allowlist → no skills executed."""
    skill = FakeSkill()
    policy = ActionPolicy(
        dry_run=False,
        allowed_events=("pull_request.opened",),
    )
    orch = _make_orchestrator(skills=[skill], policy=policy)
    event = _make_event(event_name="issues.opened")

    await orch._process_event(event)

    assert skill.execute_count == 0
    assert respx.calls.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_no_matching_skills_logged_and_skipped() -> None:
    """3. No matching skills → logged and skipped."""
    skill = FakeSkill(handled_events=frozenset({"pull_request.opened"}))
    orch = _make_orchestrator(skills=[skill])
    event = _make_event(event_name="issues.opened")

    await orch._process_event(event)

    assert skill.execute_count == 0
    assert respx.calls.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_no_installation_id_warning_logged() -> None:
    """4. No installation_id → warning logged."""
    skill = FakeSkill()
    orch = _make_orchestrator(skills=[skill])
    event = _make_event(installation_id=None)

    await orch._process_event(event)

    assert skill.execute_count == 0
    assert respx.calls.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_skill_runs_action_executes_idempotency_recorded() -> None:
    """5. Happy path: skill runs, action executes, idempotency key recorded."""
    action = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="Hello!"
    )
    skill = FakeSkill(planned_actions=(action,))
    store = InMemoryIdempotencyStore()
    orch = _make_orchestrator(skills=[skill], idempotency_store=store)

    _mock_installation_token()
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 1,
                "body": "Hello!",
                "user": {"login": "bot"},
                "created_at": "2026-04-08T12:00:00Z",
                "updated_at": "2026-04-08T12:00:00Z",
            },
        )
    )

    event = _make_event()
    await orch._process_event(event)

    assert skill.execute_count == 1
    # Write endpoint called exactly once
    write_calls = [c for c in respx.calls if c.request.method == "POST"
                   and "comments" in str(c.request.url)]
    assert len(write_calls) == 1
    # Idempotency key recorded
    key = f"{event.delivery_id}::{action.fingerprint()}"
    assert store.is_seen(key)


@pytest.mark.asyncio
@respx.mock
async def test_duplicate_delivery_skipped() -> None:
    """6. Duplicate delivery: same delivery_id + same fingerprint → second time skipped."""
    action = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="Hello!"
    )
    skill = FakeSkill(planned_actions=(action,))
    store = InMemoryIdempotencyStore()
    orch = _make_orchestrator(skills=[skill], idempotency_store=store)

    _mock_installation_token()
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 1,
                "body": "Hello!",
                "user": {"login": "bot"},
                "created_at": "2026-04-08T12:00:00Z",
                "updated_at": "2026-04-08T12:00:00Z",
            },
        )
    )

    event = _make_event()
    await orch._process_event(event)
    await orch._process_event(event)

    assert skill.execute_count == 2  # Skill runs both times
    # But the write endpoint is called only once
    write_calls = [c for c in respx.calls if c.request.method == "POST"
                   and "comments" in str(c.request.url)]
    assert len(write_calls) == 1


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_skill_executes_action_not_written() -> None:
    """7. Dry-run: skill executes, action NOT written, NOT marked seen."""
    action = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="Hello!"
    )
    skill = FakeSkill(planned_actions=(action,))
    store = InMemoryIdempotencyStore()
    policy = ActionPolicy(dry_run=True)
    orch = _make_orchestrator(skills=[skill], policy=policy, idempotency_store=store)

    _mock_installation_token()

    event = _make_event()
    await orch._process_event(event)

    assert skill.execute_count == 1
    # No write calls at all (only installation token POST)
    write_calls = [c for c in respx.calls if c.request.method == "POST"
                   and "comments" in str(c.request.url)]
    assert len(write_calls) == 0
    # Not marked seen
    key = f"{event.delivery_id}::{action.fingerprint()}"
    assert not store.is_seen(key)


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_is_seen_still_false() -> None:
    """8. After dry-run skip, is_seen still returns False."""
    action = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="Hello!"
    )
    skill = FakeSkill(planned_actions=(action,))
    store = InMemoryIdempotencyStore()
    policy = ActionPolicy(dry_run=True)
    orch = _make_orchestrator(skills=[skill], policy=policy, idempotency_store=store)

    _mock_installation_token()

    event = _make_event()
    await orch._process_event(event)
    await orch._process_event(event)

    key = f"{event.delivery_id}::{action.fingerprint()}"
    assert not store.is_seen(key)


@pytest.mark.asyncio
@respx.mock
async def test_write_failure_action_not_marked_seen() -> None:
    """9. Write failure (mock 500) → action not marked seen, remaining actions still execute."""
    action1 = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="First"
    )
    action2 = AddLabelsAction(
        owner="octocat", repo="hello-world", issue_number=101, labels=("bug",)
    )
    skill = FakeSkill(planned_actions=(action1, action2))
    store = InMemoryIdempotencyStore()
    orch = _make_orchestrator(skills=[skill], idempotency_store=store)

    _mock_installation_token()
    # First action fails with 500
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    # Second action succeeds
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/labels").mock(
        return_value=httpx.Response(200, json=[{"name": "bug"}])
    )

    event = _make_event()
    await orch._process_event(event)

    # First action NOT marked seen
    key1 = f"{event.delivery_id}::{action1.fingerprint()}"
    assert not store.is_seen(key1)
    # Second action IS marked seen
    key2 = f"{event.delivery_id}::{action2.fingerprint()}"
    assert store.is_seen(key2)


@pytest.mark.asyncio
@respx.mock
async def test_two_actions_first_fails_second_still_executes() -> None:
    """10. Two actions from same event, first fails → second still executes."""
    action1 = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="First"
    )
    action2 = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=102, body="Second"
    )
    skill = FakeSkill(planned_actions=(action1, action2))
    store = InMemoryIdempotencyStore()
    orch = _make_orchestrator(skills=[skill], idempotency_store=store)

    _mock_installation_token()
    # First action fails
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(500, text="Internal Server Error")
    )
    # Second action succeeds
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/102/comments").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 2,
                "body": "Second",
                "user": {"login": "bot"},
                "created_at": "2026-04-08T12:00:00Z",
                "updated_at": "2026-04-08T12:00:00Z",
            },
        )
    )

    event = _make_event()
    await orch._process_event(event)

    # Both endpoints were called
    calls_101 = [c for c in respx.calls if "101/comments" in str(c.request.url)]
    calls_102 = [c for c in respx.calls if "102/comments" in str(c.request.url)]
    assert len(calls_101) == 1
    assert len(calls_102) == 1

    # Only second is marked seen
    key1 = f"{event.delivery_id}::{action1.fingerprint()}"
    key2 = f"{event.delivery_id}::{action2.fingerprint()}"
    assert not store.is_seen(key1)
    assert store.is_seen(key2)


@pytest.mark.asyncio
@respx.mock
async def test_two_matching_skills_both_run_both_actions_execute() -> None:
    """11. issues.opened with two matching skills → both skills run, both actions execute."""
    action1 = AddLabelsAction(
        owner="octocat", repo="hello-world", issue_number=101, labels=("bug",)
    )
    action2 = IssueCommentAction(
        owner="octocat", repo="hello-world", issue_number=101, body="Thanks!"
    )
    skill1 = FakeSkill(skill_name="labeler", planned_actions=(action1,))
    skill2 = FakeSkill(skill_name="responder", planned_actions=(action2,))
    store = InMemoryIdempotencyStore()
    orch = _make_orchestrator(skills=[skill1, skill2], idempotency_store=store)

    _mock_installation_token()
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/labels").mock(
        return_value=httpx.Response(200, json=[{"name": "bug"}])
    )
    respx.post(f"{BASE}/repos/octocat/hello-world/issues/101/comments").mock(
        return_value=httpx.Response(
            201,
            json={
                "id": 1,
                "body": "Thanks!",
                "user": {"login": "bot"},
                "created_at": "2026-04-08T12:00:00Z",
                "updated_at": "2026-04-08T12:00:00Z",
            },
        )
    )

    event = _make_event()
    await orch._process_event(event)

    assert skill1.execute_count == 1
    assert skill2.execute_count == 1

    label_calls = [c for c in respx.calls if "labels" in str(c.request.url)]
    comment_calls = [c for c in respx.calls if "comments" in str(c.request.url)]
    assert len(label_calls) == 1
    assert len(comment_calls) == 1

    key1 = f"{event.delivery_id}::{action1.fingerprint()}"
    key2 = f"{event.delivery_id}::{action2.fingerprint()}"
    assert store.is_seen(key1)
    assert store.is_seen(key2)
