"""Full orchestrator replacing the Phase 3 skill dispatcher.

Adds allowlist gating, idempotency, dry-run mode, and action execution
on top of the same dequeue-loop pattern as ``SkillDispatcher``.
"""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import Any

import structlog

from github_auto_maintainer.core.action_policy import ActionPolicy
from github_auto_maintainer.core.actions import (
    ActionRequest,
    AddLabelsAction,
    CommitPatchAction,
    CreateBranchAction,
    CreatePullRequestAction,
    IssueCommentAction,
    PRReviewSummaryAction,
)
from github_auto_maintainer.core.idempotency import (
    IdempotencyStore,
    build_idempotency_key,
)
from github_auto_maintainer.core.job_queue import JobQueue
from github_auto_maintainer.core.llm_router import LLMRouter
from github_auto_maintainer.core.logging_utils import redact_mapping
from github_auto_maintainer.github.auth import (
    fetch_installation_access_token,
    generate_github_app_jwt,
)
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.errors import GitHubClientError
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult


class Orchestrator:
    """Consumes events from the queue, runs skills, and executes write actions."""

    def __init__(
        self,
        queue: JobQueue[NormalizedEvent],
        skills: Sequence[BaseSkill],
        router: LLMRouter,
        app_id: str,
        private_key_pem: str,
        policy: ActionPolicy,
        idempotency_store: IdempotencyStore,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        self._queue = queue
        self._skills = skills
        self._router = router
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._policy = policy
        self._idempotency_store = idempotency_store
        self._logger = logger

    async def run(self) -> None:
        """Continuously dequeue events and process them."""
        self._logger.info("orchestrator.started", skill_count=len(self._skills))
        while True:
            event = await self._queue.dequeue()
            try:
                await self._process_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    "orchestrator.event_error",
                    delivery_id=event.delivery_id,
                    event_name=event.event_name,
                )

    async def _process_event(self, event: NormalizedEvent) -> None:
        # 1. Allowlist check — before any skill execution or API calls.
        if not self._policy.is_repo_allowed(event.repository_full_name):
            self._logger.info(
                "orchestrator.event_skipped_allowlist",
                delivery_id=event.delivery_id,
                repository=event.repository_full_name,
                reason="repository_not_allowed",
            )
            return

        if not self._policy.is_event_allowed(event.event_name):
            self._logger.info(
                "orchestrator.event_skipped_allowlist",
                delivery_id=event.delivery_id,
                event_name=event.event_name,
                reason="event_not_allowed",
            )
            return

        # 2. Skill matching.
        matching = [s for s in self._skills if s.handles_event(event)]
        if not matching:
            self._logger.debug(
                "orchestrator.event_skipped_no_skills",
                delivery_id=event.delivery_id,
                event_name=event.event_name,
            )
            return

        # 3. Installation check.
        if event.installation_id is None:
            self._logger.warning(
                "orchestrator.event_skipped_no_installation",
                delivery_id=event.delivery_id,
                event_name=event.event_name,
            )
            return

        # 4. Fetch token + build client.
        app_jwt = generate_github_app_jwt(
            app_id=self._app_id,
            private_key_pem=self._private_key_pem,
        )
        token = await fetch_installation_access_token(
            app_jwt=app_jwt,
            installation_id=event.installation_id,
        )

        async with GitHubClient(token=token.token) as client:
            context = SkillContext(
                event=event,
                github_client=client,
                router=self._router,
                logger=self._logger,
            )

            # 5. Execute skills — collect all planned actions.
            all_actions: list[ActionRequest] = []
            for skill in matching:
                try:
                    result = await skill.execute(context)
                    _log_skill_result(self._logger, event, result)
                    all_actions.extend(result.planned_actions)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.exception(
                        "orchestrator.skill_error",
                        delivery_id=event.delivery_id,
                        skill_name=skill.name,
                        event_name=event.event_name,
                    )

            # 6. Execute actions.
            for action in all_actions:
                await self._execute_action(event, client, action)

    async def _execute_action(
        self,
        event: NormalizedEvent,
        client: GitHubClient,
        action: ActionRequest,
    ) -> None:
        # 1. Build idempotency key.
        key = build_idempotency_key(event.delivery_id, action)

        # 2. Check idempotency.
        if self._idempotency_store.is_seen(key):
            self._logger.info(
                "orchestrator.action_skipped_idempotent",
                delivery_id=event.delivery_id,
                repository=event.repository_full_name,
                event_name=event.event_name,
                action_type=action.action_type,
                fingerprint=action.fingerprint(),
            )
            return

        # 3. Dry-run check — do NOT mark seen.
        if self._policy.dry_run:
            self._logger.info(
                "orchestrator.action_skipped_dry_run",
                delivery_id=event.delivery_id,
                repository=event.repository_full_name,
                event_name=event.event_name,
                action_type=action.action_type,
                fingerprint=action.fingerprint(),
            )
            return

        # 4. Execute write.
        try:
            match action:
                case IssueCommentAction():
                    await client.create_issue_comment(
                        action.owner, action.repo, action.issue_number, action.body
                    )
                case AddLabelsAction():
                    await client.add_labels(
                        action.owner, action.repo, action.issue_number, action.labels
                    )
                case PRReviewSummaryAction():
                    await client.create_pr_review_summary(
                        action.owner,
                        action.repo,
                        action.pr_number,
                        action.body,
                        event=action.event,
                        commit_id=action.commit_id,
                    )
                case CreateBranchAction():
                    await client.create_branch(
                        action.owner, action.repo, action.branch_name, action.from_sha
                    )
                case CommitPatchAction():
                    # Recording-only action — the actual git operations are performed
                    # by the skill directly via git_ops functions during execute().
                    # The orchestrator records this action for idempotency and audit
                    # purposes but does not make any API calls.
                    pass
                case CreatePullRequestAction():
                    await client.create_pull_request(
                        action.owner,
                        action.repo,
                        action.title,
                        action.body,
                        action.head_branch,
                        action.base_branch,
                    )
                case _:
                    self._logger.warning(
                        "orchestrator.action_unknown_type",
                        delivery_id=event.delivery_id,
                        action_type=action.action_type,
                    )
                    return
        except GitHubClientError:
            # 6. On failure: log, do NOT mark seen, do NOT re-raise.
            self._logger.exception(
                "orchestrator.action_failed",
                delivery_id=event.delivery_id,
                repository=event.repository_full_name,
                event_name=event.event_name,
                action_type=action.action_type,
                fingerprint=action.fingerprint(),
            )
            return

        # 5. On success: mark seen + log.
        self._idempotency_store.mark_seen(key)
        self._logger.info(
            "orchestrator.action_executed",
            delivery_id=event.delivery_id,
            repository=event.repository_full_name,
            event_name=event.event_name,
            action_type=action.action_type,
            fingerprint=action.fingerprint(),
            outcome="executed",
        )


def _log_skill_result(
    logger: structlog.stdlib.BoundLogger,
    event: NormalizedEvent,
    result: SkillResult[Any],
) -> None:
    """Log a skill result as structured JSON using redaction-safe handling."""
    decision_data: dict[str, object]
    if dataclasses.is_dataclass(result.decision) and not isinstance(result.decision, type):
        decision_data = redact_mapping(
            {k: v for k, v in dataclasses.asdict(result.decision).items()}
        )
    else:
        decision_data = {"raw": str(result.decision)}

    logger.info(
        "orchestrator.skill_executed",
        event_type=event.event_name,
        delivery_id=event.delivery_id,
        repository=event.repository_full_name,
        skill_name=result.skill_name,
        model=result.model_used,
        task_type=result.task_type_used.value,
        complexity=result.complexity_used.value,
        confidence=result.confidence,
        elapsed_seconds=result.elapsed_seconds,
        decision=decision_data,
        recommended_actions=list(result.recommended_actions),
        planned_actions_count=len(result.planned_actions),
        escalation_count=result.escalation_count,
    )
