"""Action request types for Phase 4 write operations."""

from __future__ import annotations

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
        return f"issue_comment:{self.owner}/{self.repo}#{self.issue_number}"


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
        return f"pr_review_summary:{self.owner}/{self.repo}#{self.pr_number}"
