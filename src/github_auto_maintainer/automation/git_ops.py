"""Git operations via GitHub REST API — no subprocess git, no local clone."""

from __future__ import annotations

import base64
from collections.abc import Sequence
from dataclasses import dataclass

from github_auto_maintainer.github.client import (
    BranchRef,
    CreatedPullRequest,
    FileCommitResult,
    GitHubClient,
)


@dataclass(frozen=True, slots=True)
class PatchFile:
    """A file to create or update as part of an auto-fix patch."""

    path: str
    new_content: str
    original_sha: str | None  # None = new file


@dataclass(frozen=True, slots=True)
class AutoFixBranch:
    """Result of creating a fix branch and committing patches."""

    branch_name: str
    branch_ref: BranchRef
    commit_results: tuple[FileCommitResult, ...]
    files_changed: int


def generate_branch_name(issue_number: int, prefix: str = "auto-fix") -> str:
    """Generate a deterministic branch name for an auto-fix."""
    return f"{prefix}/issue-{issue_number}"


async def create_fix_branch(
    client: GitHubClient,
    owner: str,
    repo: str,
    issue_number: int,
    base_branch: str = "main",
) -> AutoFixBranch:
    """Create a new branch for an auto-fix from the base branch HEAD."""
    branch_name = generate_branch_name(issue_number)
    base_ref = await client.get_branch_ref(owner, repo, base_branch)
    branch_ref = await client.create_branch(owner, repo, branch_name, base_ref.sha)
    return AutoFixBranch(
        branch_name=branch_name,
        branch_ref=branch_ref,
        commit_results=(),
        files_changed=0,
    )


async def apply_patches(
    client: GitHubClient,
    owner: str,
    repo: str,
    branch_name: str,
    patches: Sequence[PatchFile],
    commit_message: str,
) -> tuple[FileCommitResult, ...]:
    """Apply patch files to a branch via the contents API."""
    results: list[FileCommitResult] = []
    for patch in patches:
        content_b64 = base64.b64encode(patch.new_content.encode("utf-8")).decode("ascii")
        result = await client.create_or_update_file(
            owner,
            repo,
            patch.path,
            commit_message,
            content_b64,
            sha=patch.original_sha,
            branch=branch_name,
        )
        results.append(result)
    return tuple(results)


async def open_fix_pr(
    client: GitHubClient,
    owner: str,
    repo: str,
    branch_name: str,
    base_branch: str,
    title: str,
    body: str,
) -> CreatedPullRequest:
    """Open a pull request from the fix branch to the base branch."""
    return await client.create_pull_request(
        owner, repo, title, body, branch_name, base_branch
    )
