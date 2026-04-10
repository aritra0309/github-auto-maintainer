"""Tests for the AutoFixSkill (patch worker)."""

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

from github_auto_maintainer.automation.patch_worker import AutoFixSkill
from github_auto_maintainer.core.actions import IssueCommentAction
from github_auto_maintainer.core.errors import SkillExecutionError
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.run_store import InMemoryRunStore, RunStatus
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider
from github_auto_maintainer.skills.base import SkillContext

BASE = "https://api.github.com"


# ── Helpers ───────────────────────────────────────────────────────


class _FakeProvider(BaseLLMProvider):
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


def _make_router(golden_response: str) -> LLMRouter:
    catalog = ModelCatalog(
        models=(
            ModelDescriptor(
                provider="fake",
                model="fake-model",
                context_window=8000,
                cost_tier=TaskComplexity.LOW,
                suited_for=frozenset({TaskType.PATCH_GENERATION}),
            ),
        ),
        source_path=Path("/tmp/test-catalog.yaml"),
    )
    provider = _FakeProvider(golden_response)
    return LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factories={"fake": lambda model: provider},
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )


def _can_fix_response() -> str:
    return json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {
                "path": "src/main.py",
                "action": "modify",
                "new_content": "print('fixed')\n",
                "reasoning": "Fix the bug",
            }
        ],
        "commit_message": "fix: resolve issue #42",
        "confidence": "high",
        "explanation": "Simple one-line fix",
    })


def _cannot_fix_response() -> str:
    return json.dumps({
        "can_fix": False,
        "rejection_reason": "Too complex to fix automatically",
        "files_to_modify": [],
        "commit_message": "",
        "confidence": "low",
        "explanation": "This issue requires architectural changes",
    })


def _blocked_path_response() -> str:
    return json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {
                "path": ".env",
                "action": "modify",
                "new_content": "SECRET=bad\n",
                "reasoning": "Add secret",
            }
        ],
        "commit_message": "fix: add secret",
        "confidence": "high",
        "explanation": "Fix by modifying .env",
    })


def _oversized_diff_response() -> str:
    # 600 lines of content exceeds default 500 limit
    big_content = "\n".join(f"line {i}" for i in range(600))
    return json.dumps({
        "can_fix": True,
        "rejection_reason": None,
        "files_to_modify": [
            {
                "path": "src/big.py",
                "action": "modify",
                "new_content": big_content,
                "reasoning": "Big fix",
            }
        ],
        "commit_message": "fix: big change",
        "confidence": "medium",
        "explanation": "Large fix",
    })


def _make_labeled_event(
    *,
    label_name: str = "auto-fix",
    delivery_id: str = "delivery-001",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_name="issues.labeled",
        delivery_id=delivery_id,
        github_event="issues",
        action="labeled",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload={
            "action": "labeled",
            "label": {"name": label_name},
            "issue": {
                "number": 42,
                "title": "Fix the bug",
                "body": "There is a bug in main.py",
                "state": "open",
                "user": {"login": "reporter"},
                "labels": [{"name": label_name}],
                "created_at": "2026-04-10T12:00:00Z",
                "updated_at": "2026-04-10T12:00:00Z",
            },
            "repository": {
                "id": 12345,
                "name": "hello-world",
                "full_name": "octocat/hello-world",
                "owner": {"login": "octocat"},
            },
            "installation": {"id": 9876},
            "sender": {"login": "reporter"},
        },
    )


def _make_comment_event(
    *,
    comment_body: str = "/auto-fix",
    delivery_id: str = "delivery-002",
) -> NormalizedEvent:
    return NormalizedEvent(
        event_name="issue_comment.created",
        delivery_id=delivery_id,
        github_event="issue_comment",
        action="created",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload={
            "action": "created",
            "comment": {"body": comment_body, "user": {"login": "commenter"}},
            "issue": {
                "number": 42,
                "title": "Fix the bug",
                "body": "There is a bug",
                "state": "open",
                "user": {"login": "reporter"},
                "labels": [],
                "created_at": "2026-04-10T12:00:00Z",
                "updated_at": "2026-04-10T12:00:00Z",
            },
            "repository": {
                "id": 12345,
                "name": "hello-world",
                "full_name": "octocat/hello-world",
                "owner": {"login": "octocat"},
            },
            "installation": {"id": 9876},
            "sender": {"login": "commenter"},
        },
    )


def _make_pr_event() -> NormalizedEvent:
    return NormalizedEvent(
        event_name="pull_request.opened",
        delivery_id="delivery-pr",
        github_event="pull_request",
        action="opened",
        installation_id=9876,
        repository_full_name="octocat/hello-world",
        repository_id=12345,
        received_at=datetime.now(tz=UTC),
        payload={
            "action": "opened",
            "pull_request": {"number": 1},
            "repository": {
                "id": 12345,
                "name": "hello-world",
                "full_name": "octocat/hello-world",
                "owner": {"login": "octocat"},
            },
            "installation": {"id": 9876},
            "sender": {"login": "author"},
        },
    )


def _issue_json() -> dict[str, Any]:
    return {
        "number": 42,
        "title": "Fix the bug",
        "body": "There is a bug in main.py",
        "state": "open",
        "user": {"login": "reporter"},
        "labels": [{"name": "auto-fix"}],
        "created_at": "2026-04-10T12:00:00Z",
        "updated_at": "2026-04-10T12:00:00Z",
    }


def _mock_github_for_happy_path() -> None:
    """Set up respx mocks for a successful auto-fix pipeline."""
    # GET issue
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    # GET file content (for SHA of existing file)
    respx.get(f"{BASE}/repos/octocat/hello-world/contents/src/main.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "src/main.py",
                "sha": "old_sha_123",
                "content": "cHJpbnQoJ2J1Z2d5Jyk=",
                "encoding": "base64",
                "size": 14,
            },
        )
    )
    # GET branch ref (main)
    respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/main").mock(
        return_value=httpx.Response(
            200,
            json={"ref": "refs/heads/main", "object": {"sha": "base_sha_abc"}},
        )
    )
    # POST create branch
    respx.post(f"{BASE}/repos/octocat/hello-world/git/refs").mock(
        return_value=httpx.Response(
            201,
            json={
                "ref": "refs/heads/auto-fix/issue-42",
                "object": {"sha": "base_sha_abc"},
            },
        )
    )
    # PUT file content (commit)
    respx.put(f"{BASE}/repos/octocat/hello-world/contents/src/main.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": {"path": "src/main.py", "sha": "new_sha_456"},
                "commit": {"sha": "commit_sha_789"},
            },
        )
    )
    # POST create PR
    respx.post(f"{BASE}/repos/octocat/hello-world/pulls").mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 99,
                "html_url": "https://github.com/octocat/hello-world/pull/99",
                "head": {"ref": "auto-fix/issue-42"},
                "base": {"ref": "main"},
                "title": "fix: auto-fix for #42",
            },
        )
    )


# ── handles_event ─────────────────────────────────────────────────


def test_handles_issues_labeled_auto_fix() -> None:
    skill = AutoFixSkill()
    event = _make_labeled_event(label_name="auto-fix")
    assert skill.handles_event(event) is True


def test_handles_issues_labeled_wrong_label() -> None:
    skill = AutoFixSkill()
    event = _make_labeled_event(label_name="bug")
    assert skill.handles_event(event) is False


def test_handles_issue_comment_with_command() -> None:
    skill = AutoFixSkill()
    event = _make_comment_event(comment_body="Please /auto-fix this issue")
    assert skill.handles_event(event) is True


def test_handles_issue_comment_without_command() -> None:
    skill = AutoFixSkill()
    event = _make_comment_event(comment_body="hello")
    assert skill.handles_event(event) is False


def test_handles_pull_request_event() -> None:
    skill = AutoFixSkill()
    event = _make_pr_event()
    assert skill.handles_event(event) is False


def test_handles_custom_trigger_label() -> None:
    skill = AutoFixSkill(trigger_label="fix-me")
    event = _make_labeled_event(label_name="fix-me")
    assert skill.handles_event(event) is True


def test_handles_custom_trigger_command() -> None:
    skill = AutoFixSkill(trigger_command="/fix")
    event = _make_comment_event(comment_body="/fix this")
    assert skill.handles_event(event) is True


# ── Skill properties ──────────────────────────────────────────────


def test_skill_name() -> None:
    skill = AutoFixSkill()
    assert skill.name == "auto_fix"


def test_skill_default_task_type() -> None:
    skill = AutoFixSkill()
    assert skill.default_task_type == TaskType.PATCH_GENERATION


def test_skill_default_complexity() -> None:
    skill = AutoFixSkill()
    assert skill.default_complexity == TaskComplexity.MEDIUM


# ── execute: happy path ──────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_happy_path() -> None:
    """Full pipeline: label → LLM fix → safety passes → branch + commit + PR → comment."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_can_fix_response())
    event = _make_labeled_event()

    _mock_github_for_happy_path()

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        result = await skill.execute(context)

    assert result.skill_name == "auto_fix"
    assert len(result.planned_actions) == 1
    action = result.planned_actions[0]
    assert isinstance(action, IssueCommentAction)
    assert "Auto-fix PR opened" in action.body
    assert "pull/99" in action.body

    # Verify run store
    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.PR_OPENED
    assert runs[0].pr_number == 99


# ── execute: LLM rejects ─────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_llm_rejects() -> None:
    """LLM says can_fix=false → rejection comment, no branch created."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_cannot_fix_response())
    event = _make_labeled_event()

    # Only need issue endpoint — no branch/commit/PR calls
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        result = await skill.execute(context)

    assert len(result.planned_actions) == 1
    action = result.planned_actions[0]
    assert isinstance(action, IssueCommentAction)
    assert "rejected" in action.body.lower()

    # Verify run store
    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.REJECTED


# ── execute: safety violation (blocked path) ──────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_safety_violation_blocked_path() -> None:
    """Safety violation from blocked path → rejection comment."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_blocked_path_response())
    event = _make_labeled_event()

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        result = await skill.execute(context)

    assert len(result.planned_actions) == 1
    action = result.planned_actions[0]
    assert isinstance(action, IssueCommentAction)
    assert "safety violations" in action.body.lower()
    assert "blocked_path" in action.body

    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert runs[0].status == RunStatus.REJECTED
    assert len(runs[0].safety_violations) > 0


# ── execute: safety violation (oversized diff) ────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_safety_violation_oversized_diff() -> None:
    """Safety violation from oversized diff → rejection comment."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_oversized_diff_response())
    event = _make_labeled_event()

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        result = await skill.execute(context)

    assert len(result.planned_actions) == 1
    action = result.planned_actions[0]
    assert isinstance(action, IssueCommentAction)
    assert "safety violations" in action.body.lower()

    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert runs[0].status == RunStatus.REJECTED


# ── execute: branch conflict ─────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_branch_conflict() -> None:
    """409 from create_branch → run status=FAILED, SkillExecutionError raised."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_can_fix_response())
    event = _make_labeled_event()

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/contents/src/main.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "src/main.py",
                "sha": "old_sha",
                "content": "Y29udGVudA==",
                "encoding": "base64",
                "size": 7,
            },
        )
    )
    respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/main").mock(
        return_value=httpx.Response(
            200,
            json={"ref": "refs/heads/main", "object": {"sha": "base_sha"}},
        )
    )
    # Branch creation fails with 409
    respx.post(f"{BASE}/repos/octocat/hello-world/git/refs").mock(
        return_value=httpx.Response(409, json={"message": "Reference already exists"})
    )

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        with pytest.raises(SkillExecutionError, match="conflict"):
            await skill.execute(context)

    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert runs[0].status == RunStatus.FAILED


# ── execute via comment trigger ───────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_execute_via_comment_trigger() -> None:
    """Issue comment with /auto-fix triggers the pipeline."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_cannot_fix_response())
    event = _make_comment_event(comment_body="/auto-fix please")

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        result = await skill.execute(context)

    assert len(result.planned_actions) == 1
    assert isinstance(result.planned_actions[0], IssueCommentAction)
    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert len(runs) == 1


# ── run store persistence across execute ──────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_run_store_records_model_used() -> None:
    """Run store records the model used by the LLM."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_cannot_fix_response())
    event = _make_labeled_event()

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/42").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        await skill.execute(context)

    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert runs[0].model_used == "fake-model"


@pytest.mark.asyncio
@respx.mock
async def test_happy_path_records_patch_counts() -> None:
    """Run store records patch file count and line count on success."""
    run_store = InMemoryRunStore()
    skill = AutoFixSkill(run_store=run_store)
    router = _make_router(_can_fix_response())
    event = _make_labeled_event()

    _mock_github_for_happy_path()

    async with GitHubClient(token="test-token") as client:
        context = SkillContext(
            event=event,
            github_client=client,
            router=router,
            logger=structlog.get_logger(),
        )
        await skill.execute(context)

    runs = await run_store.get_runs_for_issue("octocat", "hello-world", 42)
    assert runs[0].patch_files_count == 1
    assert runs[0].patch_lines_changed > 0
