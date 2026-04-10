"""Auto-fix skill: generates and applies safe code patches from issue triggers."""

from __future__ import annotations

import importlib.resources
import time
import uuid
from datetime import UTC, datetime
from typing import Any

import structlog

from github_auto_maintainer.automation.git_ops import (
    PatchFile,
    apply_patches,
    create_fix_branch,
    open_fix_pr,
)
from github_auto_maintainer.automation.safety import (
    SafetyConfig,
    default_safety_config,
    validate_diff_size,
    validate_patch_paths,
)
from github_auto_maintainer.core.actions import IssueCommentAction
from github_auto_maintainer.core.errors import SkillExecutionError
from github_auto_maintainer.core.llm_types import LLMMessage
from github_auto_maintainer.core.run_store import (
    AutoFixRun,
    InMemoryRunStore,
    RunStatus,
    RunStore,
)
from github_auto_maintainer.core.task_types import TaskComplexity, TaskType
from github_auto_maintainer.github.errors import GitHubClientError, GitHubConflictError
from github_auto_maintainer.github.events import NormalizedEvent
from github_auto_maintainer.skills.base import BaseSkill, SkillContext, SkillResult
from github_auto_maintainer.skills.decisions import (
    PatchGenerationDecision,
    make_decision_validator,
)
from github_auto_maintainer.skills.payload import (
    extract_issue_number,
    extract_repository_name,
    extract_repository_owner,
)

_logger: structlog.stdlib.BoundLogger = structlog.get_logger()


def _load_prompt_template() -> str:
    files = importlib.resources.files("github_auto_maintainer.prompts")
    return files.joinpath("auto_fix.md").read_text(encoding="utf-8")


class AutoFixSkill(BaseSkill):
    """Generate and apply safe auto-fix patches from issue triggers.

    The skill performs branch/commit/PR operations directly during execute()
    via git_ops functions, NOT via the orchestrator's action dispatch. The
    planned_actions returned contain only the follow-up IssueCommentAction.
    """

    def __init__(
        self,
        safety_config: SafetyConfig | None = None,
        run_store: RunStore | None = None,
        trigger_label: str = "auto-fix",
        trigger_command: str = "/auto-fix",
    ) -> None:
        self._safety_config = safety_config or default_safety_config()
        self._run_store: RunStore = run_store or InMemoryRunStore()
        self._trigger_label = trigger_label
        self._trigger_command = trigger_command

    @property
    def name(self) -> str:
        return "auto_fix"

    @property
    def description(self) -> str:
        return "Generate safe auto-fix PRs from issue triggers."

    @property
    def default_task_type(self) -> TaskType:
        return TaskType.PATCH_GENERATION

    @property
    def default_complexity(self) -> TaskComplexity:
        return TaskComplexity.MEDIUM

    def handles_event(self, event: NormalizedEvent) -> bool:
        if event.event_name == "issues.labeled":
            label: dict[str, Any] = event.payload.get("label") or {}
            return str(label.get("name", "")) == self._trigger_label

        if event.event_name == "issue_comment.created":
            comment: dict[str, Any] = event.payload.get("comment") or {}
            body = str(comment.get("body", ""))
            return self._trigger_command in body

        return False

    async def execute(
        self, context: SkillContext
    ) -> SkillResult[PatchGenerationDecision]:
        start = time.monotonic()
        event = context.event
        payload = event.payload

        owner = extract_repository_owner(payload)
        repo = extract_repository_name(payload)
        issue_number = extract_issue_number(payload)

        # 1. Create run record
        run_id = str(uuid.uuid4())
        now = datetime.now(tz=UTC).isoformat()
        run = AutoFixRun(
            run_id=run_id,
            delivery_id=event.delivery_id,
            issue_number=issue_number,
            owner=owner,
            repo=repo,
            status=RunStatus.PENDING,
            branch_name=None,
            pr_number=None,
            pr_url=None,
            model_used=None,
            patch_files_count=0,
            patch_lines_changed=0,
            safety_violations=(),
            error_message=None,
            created_at=now,
            completed_at=None,
        )
        await self._run_store.create_run(run)

        # 2. Fetch issue details
        try:
            issue = await context.github_client.get_issue(owner, repo, issue_number)
        except GitHubClientError as exc:
            await self._run_store.update_run(
                run_id, status=RunStatus.FAILED, error_message=str(exc),
                completed_at=datetime.now(tz=UTC).isoformat(),
            )
            raise SkillExecutionError(
                f"Failed to fetch issue {owner}/{repo}#{issue_number}: {exc}"
            ) from exc

        # 3. Build prompt
        template = _load_prompt_template()
        prompt = template.format(
            issue_number=issue.number,
            issue_title=issue.title,
            issue_body=issue.body or "(no description)",
            file_tree="(not available — remote-only mode)",
            referenced_files="(none)",
        )

        # 4. LLM call with escalation
        task_type = TaskType.PATCH_GENERATION
        complexity = TaskComplexity.MEDIUM
        validator = make_decision_validator(PatchGenerationDecision)
        messages: list[LLMMessage] = [{"role": "user", "content": prompt}]

        response = await context.router.complete_with_escalation(
            "You are an automated code fix assistant. Respond with raw JSON only.",
            messages,
            4096,
            0.2,
            task_type,
            complexity,
            validator,
        )

        await self._run_store.update_run(run_id, model_used=response.model)

        # 5. Parse decision
        try:
            decision = PatchGenerationDecision.from_llm_response(response.content)
        except Exception as exc:
            await self._run_store.update_run(
                run_id, status=RunStatus.FAILED, error_message=str(exc),
                completed_at=datetime.now(tz=UTC).isoformat(),
            )
            raise SkillExecutionError(
                f"Failed to parse patch generation decision: {exc}"
            ) from exc

        # 6. Check if LLM says it can't fix
        if not decision.can_fix:
            await self._run_store.update_run(
                run_id, status=RunStatus.REJECTED,
                error_message=decision.rejection_reason,
                completed_at=datetime.now(tz=UTC).isoformat(),
            )
            rejection_body = (
                f"🤖 **Auto-fix rejected** for issue #{issue_number}\n\n"
                f"**Reason:** {decision.rejection_reason or 'No reason provided'}\n\n"
                f"**Explanation:** {decision.explanation}"
            )
            elapsed = time.monotonic() - start
            return SkillResult(
                skill_name=self.name,
                event_delivery_id=event.delivery_id,
                decision=decision,
                confidence=0.8 if validator(response) else 0.5,
                reasoning=decision.explanation,
                recommended_actions=("rejected",),
                model_used=response.model,
                task_type_used=task_type,
                complexity_used=complexity,
                elapsed_seconds=round(elapsed, 3),
                planned_actions=(
                    IssueCommentAction(
                        owner=owner, repo=repo,
                        issue_number=issue_number, body=rejection_body,
                    ),
                ),
            )

        # 7. Safety validation
        file_paths = [f.path for f in decision.files_to_modify]
        total_lines = sum(
            len(f.new_content.splitlines()) for f in decision.files_to_modify
        )

        path_violations = validate_patch_paths(self._safety_config, file_paths)
        size_violations = validate_diff_size(
            self._safety_config, total_lines, len(file_paths)
        )
        all_violations = path_violations + size_violations

        if all_violations:
            violation_strs = tuple(
                f"{v.rule}: {v.detail}" for v in all_violations
            )
            await self._run_store.update_run(
                run_id, status=RunStatus.REJECTED,
                safety_violations=violation_strs,
                patch_files_count=len(file_paths),
                patch_lines_changed=total_lines,
                completed_at=datetime.now(tz=UTC).isoformat(),
            )
            violations_text = "\n".join(f"- {s}" for s in violation_strs)
            safety_body = (
                f"🤖 **Auto-fix rejected** for issue #{issue_number} "
                f"due to safety violations:\n\n{violations_text}"
            )
            elapsed = time.monotonic() - start
            return SkillResult(
                skill_name=self.name,
                event_delivery_id=event.delivery_id,
                decision=decision,
                confidence=0.8 if validator(response) else 0.5,
                reasoning=decision.explanation,
                recommended_actions=("safety_rejected",),
                model_used=response.model,
                task_type_used=task_type,
                complexity_used=complexity,
                elapsed_seconds=round(elapsed, 3),
                planned_actions=(
                    IssueCommentAction(
                        owner=owner, repo=repo,
                        issue_number=issue_number, body=safety_body,
                    ),
                ),
            )

        # 8. Perform git operations
        await self._run_store.update_run(run_id, status=RunStatus.PATCHING)

        patches = [
            PatchFile(
                path=f.path,
                new_content=f.new_content,
                original_sha=None if f.action == "create" else None,
            )
            for f in decision.files_to_modify
        ]

        # For existing files, fetch their current SHA
        for i, spec in enumerate(decision.files_to_modify):
            if spec.action == "modify":
                try:
                    existing = await context.github_client.get_file_content(
                        owner, repo, spec.path
                    )
                    patches[i] = PatchFile(
                        path=spec.path,
                        new_content=spec.new_content,
                        original_sha=existing.sha,
                    )
                except GitHubClientError:
                    # File doesn't exist or can't be read — treat as new
                    pass

        try:
            fix_branch = await create_fix_branch(
                context.github_client, owner, repo, issue_number
            )
            branch_name = fix_branch.branch_name

            await self._run_store.update_run(run_id, branch_name=branch_name)

            commit_results = await apply_patches(
                context.github_client, owner, repo, branch_name,
                patches, decision.commit_message,
            )

            pr_body = (
                f"Automated fix for #{issue_number}\n\n"
                f"**Approach:** {decision.explanation}\n\n"
                f"**Confidence:** {decision.confidence}\n\n"
                f"**Files changed:** {len(commit_results)}\n\n"
                f"---\n"
                f"_This PR was generated automatically. Please review carefully before merging._"
            )

            created_pr = await open_fix_pr(
                context.github_client, owner, repo, branch_name, "main",
                f"fix: auto-fix for #{issue_number} — {issue.title}",
                pr_body,
            )

        except GitHubConflictError as exc:
            await self._run_store.update_run(
                run_id, status=RunStatus.FAILED,
                error_message=f"Branch conflict: {exc}",
                patch_files_count=len(patches),
                patch_lines_changed=total_lines,
                completed_at=datetime.now(tz=UTC).isoformat(),
            )
            raise SkillExecutionError(
                f"Branch conflict during auto-fix for {owner}/{repo}#{issue_number}: {exc}"
            ) from exc
        except GitHubClientError as exc:
            await self._run_store.update_run(
                run_id, status=RunStatus.FAILED,
                error_message=str(exc),
                patch_files_count=len(patches),
                patch_lines_changed=total_lines,
                completed_at=datetime.now(tz=UTC).isoformat(),
            )
            raise SkillExecutionError(
                f"GitHub API error during auto-fix for {owner}/{repo}#{issue_number}: {exc}"
            ) from exc

        # 9. Update run store with success
        await self._run_store.update_run(
            run_id,
            status=RunStatus.PR_OPENED,
            pr_number=created_pr.number,
            pr_url=created_pr.html_url,
            patch_files_count=len(patches),
            patch_lines_changed=total_lines,
            completed_at=datetime.now(tz=UTC).isoformat(),
        )

        # 10. Return follow-up comment action
        comment_body = (
            f"🤖 **Auto-fix PR opened:** {created_pr.html_url}\n\n"
            f"**Branch:** `{branch_name}`\n"
            f"**Files changed:** {len(patches)}\n"
            f"**Confidence:** {decision.confidence}\n\n"
            f"Please review and merge if the fix looks correct."
        )

        elapsed = time.monotonic() - start
        return SkillResult(
            skill_name=self.name,
            event_delivery_id=event.delivery_id,
            decision=decision,
            confidence=0.9 if decision.confidence == "high" else 0.7,
            reasoning=decision.explanation,
            recommended_actions=(f"pr_opened:{created_pr.number}",),
            model_used=response.model,
            task_type_used=task_type,
            complexity_used=complexity,
            elapsed_seconds=round(elapsed, 3),
            planned_actions=(
                IssueCommentAction(
                    owner=owner, repo=repo,
                    issue_number=issue_number, body=comment_body,
                ),
            ),
        )
