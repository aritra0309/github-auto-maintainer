"""Tests for git operations via GitHub REST API."""

from __future__ import annotations

import base64

import httpx
import pytest
import respx

from github_auto_maintainer.automation.git_ops import (
    AutoFixBranch,
    PatchFile,
    apply_patches,
    create_fix_branch,
    generate_branch_name,
    open_fix_pr,
)
from github_auto_maintainer.github.client import GitHubClient
from github_auto_maintainer.github.errors import (
    GitHubConflictError,
    GitHubResourceNotFoundError,
)

BASE = "https://api.github.com"


# ── Helpers ──────────────────────────────────────────────────────────


def _make_client() -> GitHubClient:
    return GitHubClient(token="ghs_test_token_123")


# ── generate_branch_name ─────────────────────────────────────────────


def test_generate_branch_name_default() -> None:
    assert generate_branch_name(42) == "auto-fix/issue-42"


def test_generate_branch_name_custom_prefix() -> None:
    assert generate_branch_name(7, prefix="hotfix") == "hotfix/issue-7"


def test_generate_branch_name_zero() -> None:
    assert generate_branch_name(0) == "auto-fix/issue-0"


# ── create_fix_branch ───────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_create_fix_branch_happy_path() -> None:
    client = _make_client()
    async with client:
        # Mock get_branch_ref (GET /repos/.../git/ref/heads/main)
        respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/main").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ref": "refs/heads/main",
                    "object": {"sha": "abc123", "type": "commit"},
                },
            )
        )
        # Mock create_branch (POST /repos/.../git/refs)
        respx.post(f"{BASE}/repos/octocat/hello-world/git/refs").mock(
            return_value=httpx.Response(
                201,
                json={
                    "ref": "refs/heads/auto-fix/issue-42",
                    "object": {"sha": "abc123", "type": "commit"},
                },
            )
        )

        result = await create_fix_branch(client, "octocat", "hello-world", 42)
        assert isinstance(result, AutoFixBranch)
        assert result.branch_name == "auto-fix/issue-42"
        assert result.branch_ref.sha == "abc123"
        assert result.commit_results == ()
        assert result.files_changed == 0


@pytest.mark.asyncio
@respx.mock
async def test_create_fix_branch_custom_base() -> None:
    client = _make_client()
    async with client:
        respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/develop").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ref": "refs/heads/develop",
                    "object": {"sha": "def456", "type": "commit"},
                },
            )
        )
        respx.post(f"{BASE}/repos/octocat/hello-world/git/refs").mock(
            return_value=httpx.Response(
                201,
                json={
                    "ref": "refs/heads/auto-fix/issue-10",
                    "object": {"sha": "def456", "type": "commit"},
                },
            )
        )

        result = await create_fix_branch(
            client, "octocat", "hello-world", 10, base_branch="develop"
        )
        assert result.branch_name == "auto-fix/issue-10"
        assert result.branch_ref.sha == "def456"


@pytest.mark.asyncio
@respx.mock
async def test_create_fix_branch_conflict_409() -> None:
    client = _make_client()
    async with client:
        respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/main").mock(
            return_value=httpx.Response(
                200,
                json={
                    "ref": "refs/heads/main",
                    "object": {"sha": "abc123", "type": "commit"},
                },
            )
        )
        respx.post(f"{BASE}/repos/octocat/hello-world/git/refs").mock(
            return_value=httpx.Response(409, json={"message": "Reference already exists"})
        )

        with pytest.raises(GitHubConflictError):
            await create_fix_branch(client, "octocat", "hello-world", 42)


# ── apply_patches ────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_apply_patches_single_file() -> None:
    client = _make_client()
    async with client:
        respx.put(
            f"{BASE}/repos/octocat/hello-world/contents/src/main.py"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": {"path": "src/main.py", "sha": "new_sha_1"},
                    "commit": {"sha": "commit_sha_1"},
                },
            )
        )

        patches = [
            PatchFile(path="src/main.py", new_content="print('hello')\n", original_sha="old_sha")
        ]
        results = await apply_patches(
            client, "octocat", "hello-world", "auto-fix/issue-1", patches, "fix: test"
        )
        assert len(results) == 1
        assert results[0].path == "src/main.py"
        assert results[0].sha == "new_sha_1"
        assert results[0].commit_sha == "commit_sha_1"


@pytest.mark.asyncio
@respx.mock
async def test_apply_patches_multi_file() -> None:
    client = _make_client()
    async with client:
        respx.put(
            f"{BASE}/repos/octocat/hello-world/contents/src/a.py"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": {"path": "src/a.py", "sha": "sha_a"},
                    "commit": {"sha": "commit_a"},
                },
            )
        )
        respx.put(
            f"{BASE}/repos/octocat/hello-world/contents/src/b.py"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": {"path": "src/b.py", "sha": "sha_b"},
                    "commit": {"sha": "commit_b"},
                },
            )
        )

        patches = [
            PatchFile(path="src/a.py", new_content="a\n", original_sha="old_a"),
            PatchFile(path="src/b.py", new_content="b\n", original_sha="old_b"),
        ]
        results = await apply_patches(
            client, "octocat", "hello-world", "auto-fix/issue-2", patches, "fix: multi"
        )
        assert len(results) == 2


@pytest.mark.asyncio
@respx.mock
async def test_apply_patches_new_file_no_sha() -> None:
    client = _make_client()
    async with client:
        put_route = respx.put(
            f"{BASE}/repos/octocat/hello-world/contents/src/new.py"
        ).mock(
            return_value=httpx.Response(
                201,
                json={
                    "content": {"path": "src/new.py", "sha": "new_sha"},
                    "commit": {"sha": "commit_new"},
                },
            )
        )

        patches = [
            PatchFile(path="src/new.py", new_content="# new file\n", original_sha=None)
        ]
        results = await apply_patches(
            client, "octocat", "hello-world", "auto-fix/issue-3", patches, "feat: new file"
        )
        assert len(results) == 1
        # Verify that sha was not included in the request body
        import json

        request_body: dict[str, object] = json.loads(
            put_route.calls[0].request.content
        )
        assert "sha" not in request_body


@pytest.mark.asyncio
@respx.mock
async def test_apply_patches_content_is_base64() -> None:
    client = _make_client()
    async with client:
        put_route = respx.put(
            f"{BASE}/repos/octocat/hello-world/contents/src/file.py"
        ).mock(
            return_value=httpx.Response(
                200,
                json={
                    "content": {"path": "src/file.py", "sha": "s"},
                    "commit": {"sha": "c"},
                },
            )
        )

        content = "hello world\n"
        patches = [PatchFile(path="src/file.py", new_content=content, original_sha="x")]
        await apply_patches(
            client, "octocat", "hello-world", "branch", patches, "msg"
        )

        import json

        body: dict[str, object] = json.loads(put_route.calls[0].request.content)
        expected_b64 = base64.b64encode(content.encode("utf-8")).decode("ascii")
        assert body["content"] == expected_b64


# ── open_fix_pr ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_open_fix_pr_happy_path() -> None:
    client = _make_client()
    async with client:
        respx.post(f"{BASE}/repos/octocat/hello-world/pulls").mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 99,
                    "html_url": "https://github.com/octocat/hello-world/pull/99",
                    "head": {"ref": "auto-fix/issue-42"},
                    "base": {"ref": "main"},
                    "title": "fix: auto-fix for #42",
                },
            )
        )

        result = await open_fix_pr(
            client,
            "octocat",
            "hello-world",
            "auto-fix/issue-42",
            "main",
            "fix: auto-fix for #42",
            "PR body",
        )
        assert result.number == 99
        assert result.html_url == "https://github.com/octocat/hello-world/pull/99"
        assert result.head_ref == "auto-fix/issue-42"
        assert result.base_ref == "main"


@pytest.mark.asyncio
@respx.mock
async def test_open_fix_pr_sends_correct_payload() -> None:
    client = _make_client()
    async with client:
        pr_route = respx.post(f"{BASE}/repos/octocat/hello-world/pulls").mock(
            return_value=httpx.Response(
                201,
                json={
                    "number": 1,
                    "html_url": "https://github.com/octocat/hello-world/pull/1",
                    "head": {"ref": "fix-branch"},
                    "base": {"ref": "main"},
                    "title": "Fix title",
                },
            )
        )

        await open_fix_pr(
            client, "octocat", "hello-world", "fix-branch", "main", "Fix title", "Fix body"
        )

        import json

        body: dict[str, object] = json.loads(pr_route.calls[0].request.content)
        assert body["title"] == "Fix title"
        assert body["body"] == "Fix body"
        assert body["head"] == "fix-branch"
        assert body["base"] == "main"


# ── Error cases ──────────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_branch_ref_404() -> None:
    client = _make_client()
    async with client:
        respx.get(f"{BASE}/repos/octocat/hello-world/git/ref/heads/nonexistent").mock(
            return_value=httpx.Response(404, json={"message": "Not Found"})
        )

        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_branch_ref("octocat", "hello-world", "nonexistent")
