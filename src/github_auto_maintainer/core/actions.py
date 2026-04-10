"""Action request types for write operations."""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import Protocol, runtime_checkable


@runtime_checkable
class ActionRequest(Protocol):
    """Protocol for all action requests.

    Each concrete action is a frozen dataclass that independently satisfies
    this protocol.  Protocol (not ABC) is used because frozen dataclasses
    cannot inherit from a frozen dataclass base that has fields.
    """

    @property
    def action_type(self) -> str: ...

    def fingerprint(self) -> str: ...


@dataclass(frozen=True, slots=True)
class IssueCommentAction:
    """Request to create a comment on an issue."""

    owner: str
    repo: str
    issue_number: int
    body: str

    @property
    def action_type(self) -> str:
        return "issue_comment"

    def fingerprint(self) -> str:
        body_hash = hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:12]
        return f"issue_comment:{self.owner}/{self.repo}#{self.issue_number}:{body_hash}"


@dataclass(frozen=True, slots=True)
class AddLabelsAction:
    """Request to add labels to an issue."""

    owner: str
    repo: str
    issue_number: int
    labels: tuple[str, ...]

    @property
    def action_type(self) -> str:
        return "add_labels"

    def fingerprint(self) -> str:
        sorted_labels = ",".join(sorted(self.labels))
        return f"add_labels:{self.owner}/{self.repo}#{self.issue_number}:{sorted_labels}"


@dataclass(frozen=True, slots=True)
class PRReviewSummaryAction:
    """Request to create a PR review summary."""

    owner: str
    repo: str
    pr_number: int
    body: str
    event: str = "COMMENT"
    commit_id: str | None = None

    @property
    def action_type(self) -> str:
        return "pr_review_summary"

    def fingerprint(self) -> str:
        body_hash = hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:12]
        return f"pr_review_summary:{self.owner}/{self.repo}#{self.pr_number}:{body_hash}"


# ── Phase 5 action types ─────────────────────────────────────


@dataclass(frozen=True, slots=True)
class PatchFileSummary:
    """Lightweight patch file summary for action fingerprinting."""

    path: str
    is_new: bool


@dataclass(frozen=True, slots=True)
class CreateBranchAction:
    """Request to create a branch."""

    owner: str
    repo: str
    branch_name: str
    from_sha: str

    @property
    def action_type(self) -> str:
        return "create_branch"

    def fingerprint(self) -> str:
        return f"create_branch:{self.owner}/{self.repo}:{self.branch_name}"


@dataclass(frozen=True, slots=True)
class CommitPatchAction:
    """Request to commit patches to a branch."""

    owner: str
    repo: str
    branch_name: str
    patches: tuple[PatchFileSummary, ...]
    commit_message: str

    @property
    def action_type(self) -> str:
        return "commit_patch"

    def fingerprint(self) -> str:
        paths = ",".join(sorted(p.path for p in self.patches))
        return f"commit_patch:{self.owner}/{self.repo}:{self.branch_name}:{paths}"


@dataclass(frozen=True, slots=True)
class CreatePullRequestAction:
    """Request to create a pull request."""

    owner: str
    repo: str
    title: str
    body: str
    head_branch: str
    base_branch: str
    issue_number: int

    @property
    def action_type(self) -> str:
        return "create_pull_request"

    def fingerprint(self) -> str:
        body_hash = hashlib.sha256(self.body.encode("utf-8")).hexdigest()[:12]
        return f"create_pr:{self.owner}/{self.repo}:{self.head_branch}:{body_hash}"
