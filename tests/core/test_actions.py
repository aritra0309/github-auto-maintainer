"""Tests for action fingerprinting (Phase 4.5 body-aware hashes)."""

from __future__ import annotations

import hashlib

from github_auto_maintainer.core.actions import (
    AddLabelsAction,
    IssueCommentAction,
    PRReviewSummaryAction,
)


def test_issue_comment_fingerprint_stable() -> None:
    """Identical inputs produce the same fingerprint."""
    a = IssueCommentAction(owner="o", repo="r", issue_number=1, body="hello")
    b = IssueCommentAction(owner="o", repo="r", issue_number=1, body="hello")
    assert a.fingerprint() == b.fingerprint()


def test_issue_comment_fingerprint_changes_with_body() -> None:
    """Different body values produce different fingerprints."""
    a = IssueCommentAction(owner="o", repo="r", issue_number=1, body="hello")
    b = IssueCommentAction(owner="o", repo="r", issue_number=1, body="world")
    assert a.fingerprint() != b.fingerprint()


def test_pr_review_summary_fingerprint_stable() -> None:
    """Identical inputs produce the same fingerprint."""
    a = PRReviewSummaryAction(owner="o", repo="r", pr_number=1, body="summary")
    b = PRReviewSummaryAction(owner="o", repo="r", pr_number=1, body="summary")
    assert a.fingerprint() == b.fingerprint()


def test_pr_review_summary_fingerprint_changes_with_body() -> None:
    """Different body values produce different fingerprints."""
    a = PRReviewSummaryAction(owner="o", repo="r", pr_number=1, body="summary A")
    b = PRReviewSummaryAction(owner="o", repo="r", pr_number=1, body="summary B")
    assert a.fingerprint() != b.fingerprint()


def test_add_labels_fingerprint_order_insensitive() -> None:
    """Label order does not affect the fingerprint."""
    a = AddLabelsAction(owner="o", repo="r", issue_number=1, labels=("bug", "urgent"))
    b = AddLabelsAction(owner="o", repo="r", issue_number=1, labels=("urgent", "bug"))
    assert a.fingerprint() == b.fingerprint()


def test_issue_comment_fingerprint_contains_body_hash() -> None:
    """The fingerprint string contains the sha256 prefix of the body."""
    body = "hello world"
    action = IssueCommentAction(owner="o", repo="r", issue_number=1, body=body)
    expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    assert expected_hash in action.fingerprint()
