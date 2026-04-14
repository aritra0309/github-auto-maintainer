"""Issue label skill: suggests and applies labels to GitHub issues."""

from __future__ import annotations

import importlib.resources
import time

from github_auto_maintainer.core.actions import AddLabelsAction
from github_auto_maintainer.core.errors import SkillExecutionError, SkillResponseParsingError
from github_auto_maintainer.core.llm_types import LLMMessage
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.errors import GitHubClientError
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult
from github_auto_maintainer.skills.decisions import IssueLabelDecision, make_decision_validator
from github_auto_maintainer.skills.payload import (
    extract_issue_number,
    extract_repository_name,
    extract_repository_owner,
)

_HANDLED_EVENTS = frozenset({"issues.opened"})


def _load_prompt_template() -> str:
    files = importlib.resources.files("github_auto_maintainer.prompts")
    return files.joinpath("issue_label.md").read_text(encoding="utf-8")


class IssueLabelSkill(BaseSkill):
    """Suggest and apply labels to incoming GitHub issues."""

    @property
    def name(self) -> str:
        return "issue_label"

    @property
    def description(self) -> str:
        return "Analyze issues and suggest appropriate labels."

    @property
    def default_task_type(self) -> TaskType:
        return TaskType.CLASSIFICATION

    @property
    def default_complexity(self) -> TaskComplexity:
        return TaskComplexity.LOW

    def handles_event(self, event: NormalizedEvent) -> bool:
        return event.event_name in _HANDLED_EVENTS

    async def execute(self, context: SkillContext) -> SkillResult[IssueLabelDecision]:
        start = time.monotonic()
        event = context.event
        payload = event.payload

        owner = extract_repository_owner(payload)
        repo = extract_repository_name(payload)
        issue_number = extract_issue_number(payload)

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

        task_type = TaskType.CLASSIFICATION
        complexity = TaskComplexity.LOW

        validator = make_decision_validator(IssueLabelDecision)
        messages: list[LLMMessage] = [{"role": "user", "content": prompt}]

        response = await context.router.complete_with_escalation(
            "You are an issue labeling assistant. Respond with raw JSON only.",
            messages,
            1024,
            0.2,
            task_type,
            complexity,
            validator,
        )

        try:
            decision = IssueLabelDecision.from_llm_response(response.content)
        except SkillResponseParsingError as exc:
            raise SkillExecutionError(
                f"Failed to parse issue label decision after escalation: {exc}"
            ) from exc

        planned_actions: tuple[AddLabelsAction, ...]
        if decision.labels:
            planned_actions = (
                AddLabelsAction(
                    owner=owner,
                    repo=repo,
                    issue_number=issue_number,
                    labels=decision.labels,
                ),
            )
        else:
            planned_actions = ()

        elapsed = time.monotonic() - start
        return SkillResult(
            skill_name=self.name,
            event_delivery_id=event.delivery_id,
            decision=decision,
            confidence=0.8 if validator(response) else 0.5,
            reasoning=decision.reasoning,
            recommended_actions=tuple(
                f"label:{label}" for label in decision.labels
            ),
            model_used=response.model,
            task_type_used=task_type,
            complexity_used=complexity,
            elapsed_seconds=round(elapsed, 3),
            planned_actions=planned_actions,
            escalation_count=response.escalation_count,
        )
