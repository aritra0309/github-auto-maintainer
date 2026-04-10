"""Tests for the in-memory idempotency store and key builder."""

from __future__ import annotations

from github_auto_maintainer.core.actions import AddLabelsAction, IssueCommentAction
from github_auto_maintainer.core.idempotency import (
    InMemoryIdempotencyStore,
    build_idempotency_key,
)


def test_fresh_store_key_not_seen() -> None:
    store = InMemoryIdempotencyStore()
    assert store.is_seen("key") is False


def test_mark_seen_then_is_seen() -> None:
    store = InMemoryIdempotencyStore()
    store.mark_seen("key")
    assert store.is_seen("key") is True


def test_different_key_still_not_seen() -> None:
    store = InMemoryIdempotencyStore()
    store.mark_seen("key-a")
    assert store.is_seen("key-b") is False


def test_double_mark_seen_does_not_raise() -> None:
    store = InMemoryIdempotencyStore()
    store.mark_seen("key")
    store.mark_seen("key")  # no exception
    assert store.is_seen("key") is True


def test_build_key_issue_comment() -> None:
    action = IssueCommentAction(
        owner="owner", repo="repo", issue_number=42, body="hello"
    )
    key = build_idempotency_key("delivery-1", action)
    assert key == "delivery-1::issue_comment:owner/repo#42:2cf24dba5fb0"


def test_build_key_add_labels_sorted() -> None:
    action = AddLabelsAction(
        owner="owner", repo="repo", issue_number=7, labels=("bug", "auth")
    )
    key = build_idempotency_key("delivery-2", action)
    assert key == "delivery-2::add_labels:owner/repo#7:auth,bug"
