"""Tests for Phase 5 action types and fingerprinting."""

from __future__ import annotations

import hashlib

from github_auto_maintainer.core.actions import (
    ActionRequest,
    CommitPatchAction,
    CreateBranchAction,
    CreatePullRequestAction,
    PatchFileSummary,
)


# ── PatchFileSummary ──────────────────────────────────────────────


def test_patch_file_summary_construction() -> None:
    pfs = PatchFileSummary(path="src/main.py", is_new=False)
    assert pfs.path == "src/main.py"
    assert pfs.is_new is False


def test_patch_file_summary_new_file() -> None:
    pfs = PatchFileSummary(path="new.py", is_new=True)
    assert pfs.is_new is True


# ── CreateBranchAction ────────────────────────────────────────────


def test_create_branch_action_type() -> None:
    action = CreateBranchAction(
        owner="o", repo="r", branch_name="auto-fix/issue-1", from_sha="abc"
    )
    assert action.action_type == "create_branch"


def test_create_branch_fingerprint_deterministic() -> None:
    a = CreateBranchAction(
        owner="o", repo="r", branch_name="auto-fix/issue-1", from_sha="abc"
    )
    b = CreateBranchAction(
        owner="o", repo="r", branch_name="auto-fix/issue-1", from_sha="abc"
    )
    assert a.fingerprint() == b.fingerprint()


def test_create_branch_fingerprint_changes_with_branch() -> None:
    a = CreateBranchAction(
        owner="o", repo="r", branch_name="auto-fix/issue-1", from_sha="abc"
    )
    b = CreateBranchAction(
        owner="o", repo="r", branch_name="auto-fix/issue-2", from_sha="abc"
    )
    assert a.fingerprint() != b.fingerprint()


def test_create_branch_is_action_request() -> None:
    action = CreateBranchAction(
        owner="o", repo="r", branch_name="branch", from_sha="sha"
    )
    assert isinstance(action, ActionRequest)


# ── CommitPatchAction ─────────────────────────────────────────────


def test_commit_patch_action_type() -> None:
    action = CommitPatchAction(
        owner="o",
        repo="r",
        branch_name="branch",
        patches=(PatchFileSummary(path="a.py", is_new=False),),
        commit_message="fix",
    )
    assert action.action_type == "commit_patch"


def test_commit_patch_fingerprint_deterministic() -> None:
    patches = (PatchFileSummary(path="a.py", is_new=False),)
    a = CommitPatchAction(
        owner="o", repo="r", branch_name="b", patches=patches, commit_message="fix"
    )
    b = CommitPatchAction(
        owner="o", repo="r", branch_name="b", patches=patches, commit_message="fix"
    )
    assert a.fingerprint() == b.fingerprint()


def test_commit_patch_fingerprint_sorted_paths() -> None:
    """Fingerprint sorts paths, so order doesn't matter."""
    p1 = (
        PatchFileSummary(path="a.py", is_new=False),
        PatchFileSummary(path="b.py", is_new=False),
    )
    p2 = (
        PatchFileSummary(path="b.py", is_new=False),
        PatchFileSummary(path="a.py", is_new=False),
    )
    a = CommitPatchAction(
        owner="o", repo="r", branch_name="b", patches=p1, commit_message="fix"
    )
    b = CommitPatchAction(
        owner="o", repo="r", branch_name="b", patches=p2, commit_message="fix"
    )
    assert a.fingerprint() == b.fingerprint()


def test_commit_patch_fingerprint_changes_with_paths() -> None:
    p1 = (PatchFileSummary(path="a.py", is_new=False),)
    p2 = (PatchFileSummary(path="b.py", is_new=False),)
    a = CommitPatchAction(
        owner="o", repo="r", branch_name="b", patches=p1, commit_message="fix"
    )
    b = CommitPatchAction(
        owner="o", repo="r", branch_name="b", patches=p2, commit_message="fix"
    )
    assert a.fingerprint() != b.fingerprint()


def test_commit_patch_is_action_request() -> None:
    action = CommitPatchAction(
        owner="o",
        repo="r",
        branch_name="b",
        patches=(PatchFileSummary(path="a.py", is_new=False),),
        commit_message="fix",
    )
    assert isinstance(action, ActionRequest)


# ── CreatePullRequestAction ───────────────────────────────────────


def test_create_pr_action_type() -> None:
    action = CreatePullRequestAction(
        owner="o",
        repo="r",
        title="fix: stuff",
        body="body text",
        head_branch="auto-fix/issue-1",
        base_branch="main",
        issue_number=1,
    )
    assert action.action_type == "create_pull_request"


def test_create_pr_fingerprint_deterministic() -> None:
    a = CreatePullRequestAction(
        owner="o", repo="r", title="t", body="body",
        head_branch="h", base_branch="b", issue_number=1,
    )
    b = CreatePullRequestAction(
        owner="o", repo="r", title="t", body="body",
        head_branch="h", base_branch="b", issue_number=1,
    )
    assert a.fingerprint() == b.fingerprint()


def test_create_pr_fingerprint_changes_with_body() -> None:
    a = CreatePullRequestAction(
        owner="o", repo="r", title="t", body="body A",
        head_branch="h", base_branch="b", issue_number=1,
    )
    b = CreatePullRequestAction(
        owner="o", repo="r", title="t", body="body B",
        head_branch="h", base_branch="b", issue_number=1,
    )
    assert a.fingerprint() != b.fingerprint()


def test_create_pr_fingerprint_contains_body_hash() -> None:
    body = "some pr body"
    action = CreatePullRequestAction(
        owner="o", repo="r", title="t", body=body,
        head_branch="h", base_branch="b", issue_number=1,
    )
    expected_hash = hashlib.sha256(body.encode("utf-8")).hexdigest()[:12]
    assert expected_hash in action.fingerprint()


def test_create_pr_is_action_request() -> None:
    action = CreatePullRequestAction(
        owner="o", repo="r", title="t", body="b",
        head_branch="h", base_branch="base", issue_number=1,
    )
    assert isinstance(action, ActionRequest)
