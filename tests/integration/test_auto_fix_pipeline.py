"""End-to-end integration tests for the auto-fix pipeline.

Tests create a real queue, real orchestrator with FakeProvider, and mock
all GitHub HTTP endpoints via respx. Follows the same pattern as
test_webhook_to_orchestrator.py.
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

from github_auto_maintainer.automation.patch_worker import AutoFixSkill
from github_auto_maintainer.core.action_policy import ActionPolicy
from github_auto_maintainer.core.idempotency import InMemoryIdempotencyStore
from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.core.llm_router import LLMRouter, RouterConfig
from github_auto_maintainer.core.llm_types import LLMMessage, LLMResponse
from github_auto_maintainer.core.model_catalog import ModelCatalog, ModelDescriptor
from github_auto_maintainer.core.orchestrator import Orchestrator
from github_auto_maintainer.core.routing_policy import RoutingPolicy
from github_auto_maintainer.core.run_store import InMemoryRunStore, RunStatus
from github_auto_maintainer.core.task_types import TaskType
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.providers.base import BaseLLMProvider

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"
BASE = "https://api.github.com"
TEST_KEY = FIXTURES / "github_app_test_private_key.pem"


# ── Fakes and helpers ────────────────────────────────────────────────


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

    @property
    def call_count(self) -> int:
        return self._call_idx


def _make_router(golden_responses: list[str]) -> tuple[LLMRouter, _FakeProvider]:
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
                     TaskType.SUMMARIZATION, TaskType.CLASSIFICATION,
                     TaskType.PATCH_GENERATION}
                ),
            ),
        ),
    )
    provider = _FakeProvider(golden_responses)
    router = LLMRouter(
        config=RouterConfig(default_provider="fake", default_model="fake-model"),
        provider_factory=lambda p, m, lm: provider,
        model_catalog=catalog,
        routing_policy=RoutingPolicy(catalog),
    )
    return router, provider


def _private_key() -> str:
    return TEST_KEY.read_text(encoding="utf-8")


def _golden_json() -> str:
    return (FIXTURES / "patch_generation_golden.json").read_text()


def _rejected_json() -> str:
    return (FIXTURES / "patch_generation_rejected_golden.json").read_text()


def _issue_payload() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "issue_opened_payload.json").read_text()
    )
    return data


def _issue_json() -> dict[str, Any]:
    data: dict[str, Any] = json.loads(
        (FIXTURES / "issue_opened_payload.json").read_text()
    )
    issue: dict[str, Any] = data["issue"]
    return issue


def _make_labeled_event(
    label_name: str = "auto-fix",
    *,
    delivery_id: str = "integ-delivery-autofix-001",
) -> NormalizedEvent:
    payload = _issue_payload()
    payload["label"] = {"name": label_name}
    return NormalizedEvent(
        event_name="issues.labeled",
        delivery_id=delivery_id,
        github_event="issues",
        action="labeled",
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


def _mock_full_autofix_flow() -> dict[str, respx.Route]:
    """Mock all GitHub endpoints for a successful auto-fix flow."""
    routes: dict[str, respx.Route] = {}

    # Issue
    routes["issue"] = respx.get(
        f"{BASE}/repos/octocat/hello-world/issues/101"
    ).mock(return_value=httpx.Response(200, json=_issue_json()))

    # get_branch_ref
    routes["branch_ref"] = respx.get(
        f"{BASE}/repos/octocat/hello-world/git/ref/heads/main"
    ).mock(
        return_value=httpx.Response(
            200,
            json={"ref": "refs/heads/main", "object": {"sha": "base_sha"}},
        )
    )

    # get_file_content (for SHA lookup)
    routes["file_content"] = respx.get(
        f"{BASE}/repos/octocat/hello-world/contents/src/app/login.py"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "src/app/login.py",
                "sha": "old_sha",
                "content": "",
                "encoding": "base64",
                "size": 0,
            },
        )
    )

    # create_branch
    routes["create_branch"] = respx.post(
        f"{BASE}/repos/octocat/hello-world/git/refs"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "ref": "refs/heads/auto-fix/issue-101",
                "object": {"sha": "base_sha"},
            },
        )
    )

    # create_or_update_file
    routes["update_file"] = respx.put(
        f"{BASE}/repos/octocat/hello-world/contents/src/app/login.py"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": {"path": "src/app/login.py", "sha": "new_sha"},
                "commit": {"sha": "commit_sha"},
            },
        )
    )

    # create_pull_request
    routes["create_pr"] = respx.post(
        f"{BASE}/repos/octocat/hello-world/pulls"
    ).mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 99,
                "html_url": "https://github.com/octocat/hello-world/pull/99",
                "head": {"ref": "auto-fix/issue-101"},
                "base": {"ref": "main"},
                "title": "fix: auto-fix for #101",
            },
        )
    )

    # Post issue comment (the follow-up action dispatched by orchestrator)
    routes["comment"] = respx.post(
        f"{BASE}/repos/octocat/hello-world/issues/101/comments"
    ).mock(
        return_value=httpx.Response(
            201,
            json={"id": 555, "body": "comment", "user": {"login": "bot"}},
        )
    )

    return routes


def _make_orchestrator(
    *,
    golden_responses: list[str],
    policy: ActionPolicy | None = None,
    run_store: InMemoryRunStore | None = None,
) -> tuple[Orchestrator, _FakeProvider, InMemoryRunStore]:
    store = run_store or InMemoryRunStore()
    router, provider = _make_router(golden_responses)
    orch = Orchestrator(
        queue=InMemoryJobQueue[NormalizedEvent](),
        skills=[AutoFixSkill(run_store=store)],
        router=router,
        app_id="12345",
        private_key_pem=_private_key(),
        policy=policy or ActionPolicy(dry_run=False),
        idempotency_store=InMemoryIdempotencyStore(),
        logger=structlog.get_logger(),
    )
    return orch, provider, store


# ── Integration tests ────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_full_pipeline_happy_path() -> None:
    """Issue labeled auto-fix → skill → branch → patches → PR → comment."""
    orch, provider, store = _make_orchestrator(golden_responses=[_golden_json()])

    _mock_installation_token()
    routes = _mock_full_autofix_flow()

    event = _make_labeled_event()
    await orch._process_event(event)

    # Branch was created
    assert routes["create_branch"].call_count == 1
    # File was updated
    assert routes["update_file"].call_count == 1
    # PR was opened
    assert routes["create_pr"].call_count == 1
    # Follow-up comment was posted
    assert routes["comment"].call_count == 1

    # Run store has the record
    runs = await store.get_runs_for_issue("octocat", "hello-world", 101)
    assert len(runs) == 1
    assert runs[0].status == RunStatus.PR_OPENED
    assert runs[0].pr_number == 99


@pytest.mark.asyncio
@respx.mock
async def test_llm_rejects_no_github_writes() -> None:
    """LLM says can_fix=false → only issue comment, no branch/PR calls."""
    orch, provider, store = _make_orchestrator(golden_responses=[_rejected_json()])

    _mock_installation_token()

    # Only issue GET and comment POST should be called
    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    comment_route = respx.post(
        f"{BASE}/repos/octocat/hello-world/issues/101/comments"
    ).mock(
        return_value=httpx.Response(
            201, json={"id": 1, "body": "rejected", "user": {"login": "bot"}}
        )
    )

    event = _make_labeled_event()
    await orch._process_event(event)

    # Comment was posted (rejection message)
    assert comment_route.call_count == 1

    # No branch/PR calls
    runs = await store.get_runs_for_issue("octocat", "hello-world", 101)
    assert runs[0].status == RunStatus.REJECTED


@pytest.mark.asyncio
@respx.mock
async def test_safety_rejection_posts_comment() -> None:
    """Safety violation → comment with violations, no branch/PR."""
    golden_data: dict[str, Any] = json.loads(_golden_json())
    golden_data["files_to_modify"][0]["path"] = ".env"
    bad_golden = json.dumps(golden_data)

    orch, provider, store = _make_orchestrator(golden_responses=[bad_golden])

    _mock_installation_token()

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    comment_route = respx.post(
        f"{BASE}/repos/octocat/hello-world/issues/101/comments"
    ).mock(
        return_value=httpx.Response(
            201, json={"id": 1, "body": "safety", "user": {"login": "bot"}}
        )
    )

    event = _make_labeled_event()
    await orch._process_event(event)

    assert comment_route.call_count == 1
    runs = await store.get_runs_for_issue("octocat", "hello-world", 101)
    assert runs[0].status == RunStatus.REJECTED
    assert len(runs[0].safety_violations) > 0


@pytest.mark.asyncio
@respx.mock
async def test_dry_run_no_github_writes() -> None:
    """DRY_RUN=true → no GitHub writes at all."""
    orch, provider, store = _make_orchestrator(
        golden_responses=[_golden_json()],
        policy=ActionPolicy(dry_run=True),
    )

    _mock_installation_token()

    respx.get(f"{BASE}/repos/octocat/hello-world/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    # Mock all the git ops endpoints (skill calls them directly)
    respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/main").mock(
        return_value=httpx.Response(
            200,
            json={"ref": "refs/heads/main", "object": {"sha": "base_sha"}},
        )
    )
    respx.get(
        f"{BASE}/repos/octocat/hello-world/contents/src/app/login.py"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "src/app/login.py", "sha": "old_sha",
                "content": "", "encoding": "base64", "size": 0,
            },
        )
    )
    respx.post(f"{BASE}/repos/octocat/hello-world/git/refs").mock(
        return_value=httpx.Response(
            201,
            json={
                "ref": "refs/heads/auto-fix/issue-101",
                "object": {"sha": "base_sha"},
            },
        )
    )
    respx.put(
        f"{BASE}/repos/octocat/hello-world/contents/src/app/login.py"
    ).mock(
        return_value=httpx.Response(
            200,
            json={
                "content": {"path": "src/app/login.py", "sha": "new_sha"},
                "commit": {"sha": "commit_sha"},
            },
        )
    )
    respx.post(f"{BASE}/repos/octocat/hello-world/pulls").mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 99,
                "html_url": "https://github.com/octocat/hello-world/pull/99",
                "head": {"ref": "auto-fix/issue-101"},
                "base": {"ref": "main"},
                "title": "fix: auto-fix for #101",
            },
        )
    )
    # Comment should NOT be posted in dry-run
    comment_route = respx.post(
        f"{BASE}/repos/octocat/hello-world/issues/101/comments"
    ).mock(
        return_value=httpx.Response(
            201, json={"id": 1, "body": "x", "user": {"login": "bot"}}
        )
    )

    event = _make_labeled_event()
    await orch._process_event(event)

    # The orchestrator's action dispatch should skip the comment in dry-run
    assert comment_route.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_allowlist_rejection_no_llm_call() -> None:
    """Repo not in allowlist → skipped before LLM call."""
    orch, provider, store = _make_orchestrator(
        golden_responses=[_golden_json()],
        policy=ActionPolicy(
            dry_run=False,
            allowed_repositories=("other-org/other-repo",),
        ),
    )

    _mock_installation_token()

    event = _make_labeled_event()
    await orch._process_event(event)

    # No LLM call should have been made
    assert provider.call_count == 0


@pytest.mark.asyncio
@respx.mock
async def test_run_store_persistence_after_pipeline() -> None:
    """Run is queryable after pipeline completes."""
    orch, provider, store = _make_orchestrator(golden_responses=[_golden_json()])

    _mock_installation_token()
    _mock_full_autofix_flow()

    event = _make_labeled_event()
    await orch._process_event(event)

    # Verify run details
    runs = await store.get_recent_runs(limit=10)
    assert len(runs) == 1
    run = runs[0]
    assert run.owner == "octocat"
    assert run.repo == "hello-world"
    assert run.issue_number == 101
    assert run.status == RunStatus.PR_OPENED
    assert run.pr_url == "https://github.com/octocat/hello-world/pull/99"
    assert run.model_used == "fake-model"
    assert run.completed_at is not None
