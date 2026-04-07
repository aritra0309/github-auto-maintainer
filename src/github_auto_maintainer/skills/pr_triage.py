"""PR triage skill: read-only analysis of pull requests."""

from __future__ import annotations

import importlib.resources
import time
from typing import Any

from github_auto_maintainer.core.errors import SkillExecutionError, SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMMessage
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.diff_parser import parse_diff
from github_auto_maintainer.github.errors import GitHubClientError
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult
from github_auto_maintainer.skills.decisions import PRTriageDecision, make_decision_validator

_HANDLED_EVENTS = frozenset({"pull_request.opened", "pull_request.synchronize"})


def _load_prompt_template() -> str:
    files = importlib.resources.files("github_auto_maintainer.prompts")
    return files.joinpath("pr_triage.md").read_text(encoding="utf-8")


def _compute_routing(
    total_changed: int, total_files: int
) -> tuple[TaskType, TaskComplexity]:
    """Option B routing: small→TRIAGE/LOW, medium→DEEP_REVIEW/MEDIUM, large→DEEP_REVIEW/HIGH."""
    if total_changed < 50 and total_files <= 3:
        return TaskType.TRIAGE, TaskComplexity.LOW
    if total_changed < 300 and total_files <= 10:
        return TaskType.DEEP_REVIEW, TaskComplexity.MEDIUM
    return TaskType.DEEP_REVIEW, TaskComplexity.HIGH


class PRTriageSkill(BaseSkill):
    """Triage incoming pull requests using LLM analysis."""

    @property
    def name(self) -> str:
        return "pr_triage"

    @property
    def description(self) -> str:
        return "Analyze and triage pull requests for priority, category, and risk."

    @property
    def default_task_type(self) -> TaskType:
        return TaskType.TRIAGE

    @property
    def default_complexity(self) -> TaskComplexity:
        return TaskComplexity.LOW

    def handles_event(self, event: NormalizedEvent) -> bool:
        return event.event_name in _HANDLED_EVENTS

    async def execute(self, context: SkillContext) -> SkillResult[PRTriageDecision]:
        start = time.monotonic()
        event = context.event
        payload: dict[str, Any] = event.payload

        owner: str = payload["repository"]["owner"]["login"]
        repo: str = payload["repository"]["name"]
        pr_number: int = payload["pull_request"]["number"]

        try:
            pr = await context.github_client.get_pull_request(owner, repo, pr_number)
            raw_diff = await context.github_client.get_pull_request_diff(owner, repo, pr_number)
        except GitHubClientError as exc:
            raise SkillExecutionError(
                f"Failed to fetch PR data for {owner}/{repo}#{pr_number}: {exc}"
            ) from exc

        file_diffs = parse_diff(raw_diff)
        total_additions = sum(f.additions for f in file_diffs)
        total_deletions = sum(f.deletions for f in file_diffs)
        total_files = len(file_diffs)
        total_changed = total_additions + total_deletions

        task_type, complexity = _compute_routing(total_changed, total_files)

        file_summary = "\n".join(
            f"  {fd.new_path or fd.old_path or '(unknown)'}: "
            f"+{fd.additions}/-{fd.deletions} ({fd.status})"
            for fd in file_diffs
        )

        template = _load_prompt_template()
        prompt = template.format(
            pr_number=pr.number,
            title=pr.title,
            author=pr.author,
            base_ref=pr.base_ref,
            head_ref=pr.head_ref,
            body=pr.body or "(no description)",
            total_files_changed=total_files,
            total_additions=total_additions,
            total_deletions=total_deletions,
            file_summary=file_summary or "(no files)",
            diff_content=raw_diff[:8000] if len(raw_diff) > 8000 else raw_diff,
        )

        validator = make_decision_validator(PRTriageDecision)
        messages: list[LLMMessage] = [{"role": "user", "content": prompt}]

        response = await context.router.complete_with_escalation(
            "You are a code review triage assistant. Respond with raw JSON only.",
            messages,
            2048,
            0.2,
            task_type,
            complexity,
            validator,
        )

        try:
            decision = PRTriageDecision.from_llm_response(response.content)
        except SkillResponseParsingError as exc:
            raise SkillExecutionError(
                f"Failed to parse PR triage decision after escalation: {exc}"
            ) from exc

        elapsed = time.monotonic() - start
        return SkillResult(
            skill_name=self.name,
            event_delivery_id=event.delivery_id,
            decision=decision,
            confidence=0.8 if validator(response) else 0.5,
            reasoning=decision.summary,
            recommended_actions=tuple(
                f"label:{label}" for label in decision.suggested_labels
            ),
            model_used=response.model,
            task_type_used=task_type,
            complexity_used=complexity,
            elapsed_seconds=round(elapsed, 3),
        )
