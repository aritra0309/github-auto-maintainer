"""Minimal event-to-skill dispatcher for Phase 3."""

from __future__ import annotations

import asyncio
import dataclasses
from collections.abc import Sequence
from typing import Any

import structlog

from github_auto_maintainer.core.job_queue import JobQueue
from github_auto_maintainer.core.llm_router import LLMRouter
from github_auto_maintainer.core.logging_utils import redact_mapping
from github_auto_maintainer.github.auth import (
    fetch_installation_access_token,
    generate_github_app_jwt,
)
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult


class SkillDispatcher:
    """Consumes events from the queue and dispatches to matching skills."""

    def __init__(
        self,
        queue: JobQueue[NormalizedEvent],
        skills: Sequence[BaseSkill],
        router: LLMRouter,
        app_id: str,
        private_key_pem: str,
        logger: structlog.stdlib.BoundLogger,
    ) -> None:
        self._queue = queue
        self._skills = skills
        self._router = router
        self._app_id = app_id
        self._private_key_pem = private_key_pem
        self._logger = logger

    async def run(self) -> None:
        """Continuously dequeue events and process them."""
        self._logger.info("skill_dispatcher.started", skill_count=len(self._skills))
        while True:
            event = await self._queue.dequeue()
            try:
                await self._process_event(event)
            except asyncio.CancelledError:
                raise
            except Exception:
                self._logger.exception(
                    "skill_dispatcher.event_error",
                    delivery_id=event.delivery_id,
                    event_name=event.event_name,
                )

    async def _process_event(self, event: NormalizedEvent) -> None:
        matching = [s for s in self._skills if s.handles_event(event)]
        if not matching:
            self._logger.debug(
                "skill_dispatcher.no_matching_skills",
                delivery_id=event.delivery_id,
                event_name=event.event_name,
            )
            return

        if event.installation_id is None:
            self._logger.warning(
                "skill_dispatcher.missing_installation_id",
                delivery_id=event.delivery_id,
                event_name=event.event_name,
            )
            return

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
            for skill in matching:
                try:
                    result = await skill.execute(context)
                    _log_skill_result(self._logger, event, result)
                except asyncio.CancelledError:
                    raise
                except Exception:
                    self._logger.exception(
                        "skill_dispatcher.skill_error",
                        delivery_id=event.delivery_id,
                        skill_name=skill.name,
                        event_name=event.event_name,
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
        "skill_dispatcher.skill_result",
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
    )
