"""Issue triage skill: read-only analysis of GitHub issues."""

from __future__ import annotations

import importlib.resources
import time
from typing import Any

from github_auto_maintainer.core.errors import SkillExecutionError, SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMMessage
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.errors import GitHubClientError
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult
from github_auto_maintainer.skills.decisions import IssueTriageDecision, make_decision_validator

_HANDLED_EVENTS = frozenset({"issues.opened"})


def _load_prompt_template() -> str:
    files = importlib.resources.files("github_auto_maintainer.prompts")
    return files.joinpath("issue_triage.md").read_text(encoding="utf-8")


class IssueTriageSkill(BaseSkill):
    """Triage incoming issues using LLM analysis."""

    @property
    def name(self) -> str:
        return "issue_triage"

    @property
    def description(self) -> str:
        return "Analyze and triage issues for priority and category."

    @property
    def default_task_type(self) -> TaskType:
        return TaskType.TRIAGE

    @property
    def default_complexity(self) -> TaskComplexity:
        return TaskComplexity.LOW

    def handles_event(self, event: NormalizedEvent) -> bool:
        return event.event_name in _HANDLED_EVENTS

    async def execute(self, context: SkillContext) -> SkillResult[IssueTriageDecision]:
        start = time.monotonic()
        event = context.event
        payload: dict[str, Any] = event.payload

        owner: str = payload["repository"]["owner"]["login"]
        repo: str = payload["repository"]["name"]
        issue_number: int = payload["issue"]["number"]

        try:
            issue = await context.github_client.get_issue(owner, repo, issue_number)
            comments = await context.github_client.get_issue_comments(
                owner, repo, issue_number
            )
        except GitHubClientError as exc:
            raise SkillExecutionError(
                f"Failed to fetch issue data for {owner}/{repo}#{issue_number}: {exc}"
            ) from exc

        existing_labels = ", ".join(issue.labels) if issue.labels else "None"
        if comments:
            recent_comments = "\n".join(
                f"  @{c.author}: {c.body}" for c in comments[:10]
            )
        else:
            recent_comments = "No comments yet"

        template = _load_prompt_template()
        prompt = template.format(
            issue_number=issue.number,
            title=issue.title,
            author=issue.author,
            body=issue.body or "(no description)",
            existing_labels=existing_labels,
            recent_comments=recent_comments,
        )

        task_type = TaskType.TRIAGE
        complexity = TaskComplexity.LOW

        validator = make_decision_validator(IssueTriageDecision)
        messages: list[LLMMessage] = [{"role": "user", "content": prompt}]

        response = await context.router.complete_with_escalation(
            "You are an issue triage assistant. Respond with raw JSON only.",
            messages,
            1024,
            0.2,
            task_type,
            complexity,
            validator,
        )

        try:
            decision = IssueTriageDecision.from_llm_response(response.content)
        except SkillResponseParsingError as exc:
            raise SkillExecutionError(
                f"Failed to parse issue triage decision after escalation: {exc}"
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
