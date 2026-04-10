"""Tests for Phase 5 GitHub client methods."""

from __future__ import annotations

from typing import Any

import httpx
import pytest
import respx

from github_auto_maintainer.github.client import (
    GitHubClient,
)
from github_auto_maintainer.github.errors import (
    GitHubAuthenticationError,
    GitHubConflictError,
    GitHubResourceNotFoundError,
    GitHubValidationError,
)

BASE = "https://api.github.com"


# ── get_file_content ──────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_happy_path() -> None:
    respx.get(f"{BASE}/repos/o/r/contents/src/main.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "src/main.py",
                "sha": "abc123",
                "content": "cHJpbnQoJ2hlbGxvJyk=\n",
                "encoding": "base64",
                "size": 14,
            },
        )
    )

    async with GitHubClient(token="test") as client:
        fc = await client.get_file_content("o", "r", "src/main.py")

    assert fc.path == "src/main.py"
    assert fc.sha == "abc123"
    assert fc.encoding == "base64"
    assert fc.size == 14


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_with_ref() -> None:
    route = respx.get(f"{BASE}/repos/o/r/contents/README.md").mock(
        return_value=httpx.Response(
            200,
            json={
                "path": "README.md",
                "sha": "def456",
                "content": "IyBSRUFETUU=",
                "encoding": "base64",
                "size": 8,
            },
        )
    )

    async with GitHubClient(token="test") as client:
        fc = await client.get_file_content("o", "r", "README.md", ref="feature-branch")

    assert fc.sha == "def456"
    # Verify ref query param was sent
    assert "ref=feature-branch" in str(route.calls[0].request.url)


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_404() -> None:
    respx.get(f"{BASE}/repos/o/r/contents/missing.py").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    async with GitHubClient(token="test") as client:
        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_file_content("o", "r", "missing.py")


# ── create_or_update_file ─────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_create_or_update_file_happy_path() -> None:
    respx.put(f"{BASE}/repos/o/r/contents/src/fix.py").mock(
        return_value=httpx.Response(
            200,
            json={
                "content": {"path": "src/fix.py", "sha": "new_sha"},
                "commit": {"sha": "commit_sha"},
            },
        )
    )

    async with GitHubClient(token="test") as client:
        result = await client.create_or_update_file(
            "o", "r", "src/fix.py", "fix: patch", "Y29udGVudA==", sha="old_sha"
        )

    assert result.path == "src/fix.py"
    assert result.sha == "new_sha"
    assert result.commit_sha == "commit_sha"


@pytest.mark.asyncio
@respx.mock
async def test_create_or_update_file_with_branch() -> None:
    route = respx.put(f"{BASE}/repos/o/r/contents/new.py").mock(
        return_value=httpx.Response(
            201,
            json={
                "content": {"path": "new.py", "sha": "sha1"},
                "commit": {"sha": "commit1"},
            },
        )
    )

    async with GitHubClient(token="test") as client:
        result = await client.create_or_update_file(
            "o", "r", "new.py", "feat: add", "Y29udGVudA==", branch="my-branch"
        )

    assert result.path == "new.py"
    import json

    body: dict[str, Any] = json.loads(route.calls[0].request.content)
    assert body["branch"] == "my-branch"


@pytest.mark.asyncio
@respx.mock
async def test_create_or_update_file_422() -> None:
    respx.put(f"{BASE}/repos/o/r/contents/bad.py").mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )

    async with GitHubClient(token="test") as client:
        with pytest.raises(GitHubValidationError):
            await client.create_or_update_file(
                "o", "r", "bad.py", "msg", "Y29udGVudA=="
            )


# ── create_branch ─────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_create_branch_happy_path() -> None:
    respx.post(f"{BASE}/repos/o/r/git/refs").mock(
        return_value=httpx.Response(
            201,
            json={
                "ref": "refs/heads/auto-fix/issue-42",
                "object": {"sha": "abc123"},
            },
        )
    )

    async with GitHubClient(token="test") as client:
        ref = await client.create_branch("o", "r", "auto-fix/issue-42", "abc123")

    assert ref.ref == "refs/heads/auto-fix/issue-42"
    assert ref.sha == "abc123"


@pytest.mark.asyncio
@respx.mock
async def test_create_branch_conflict_409() -> None:
    respx.post(f"{BASE}/repos/o/r/git/refs").mock(
        return_value=httpx.Response(409, json={"message": "Reference already exists"})
    )

    async with GitHubClient(token="test") as client:
        with pytest.raises(GitHubConflictError):
            await client.create_branch("o", "r", "branch", "sha")


@pytest.mark.asyncio
@respx.mock
async def test_create_branch_401() -> None:
    respx.post(f"{BASE}/repos/o/r/git/refs").mock(
        return_value=httpx.Response(401, json={"message": "Bad credentials"})
    )

    async with GitHubClient(token="test") as client:
        with pytest.raises(GitHubAuthenticationError):
            await client.create_branch("o", "r", "branch", "sha")


# ── get_branch_ref ────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_branch_ref_happy_path() -> None:
    respx.get(f"{BASE}/repos/o/r/git/ref/heads/main").mock(
        return_value=httpx.Response(
            200,
            json={"ref": "refs/heads/main", "object": {"sha": "abc123"}},
        )
    )

    async with GitHubClient(token="test") as client:
        ref = await client.get_branch_ref("o", "r", "main")

    assert ref.ref == "refs/heads/main"
    assert ref.sha == "abc123"


@pytest.mark.asyncio
@respx.mock
async def test_get_branch_ref_404() -> None:
    respx.get(f"{BASE}/repos/o/r/git/ref/heads/missing").mock(
        return_value=httpx.Response(404, json={"message": "Not Found"})
    )

    async with GitHubClient(token="test") as client:
        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_branch_ref("o", "r", "missing")


# ── create_pull_request ───────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_create_pull_request_happy_path() -> None:
    respx.post(f"{BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(
            201,
            json={
                "number": 99,
                "html_url": "https://github.com/o/r/pull/99",
                "head": {"ref": "auto-fix/issue-42"},
                "base": {"ref": "main"},
                "title": "fix: auto-fix #42",
            },
        )
    )

    async with GitHubClient(token="test") as client:
        pr = await client.create_pull_request(
            "o", "r", "fix: auto-fix #42", "body", "auto-fix/issue-42", "main"
        )

    assert pr.number == 99
    assert pr.html_url == "https://github.com/o/r/pull/99"
    assert pr.head_ref == "auto-fix/issue-42"
    assert pr.base_ref == "main"
    assert pr.title == "fix: auto-fix #42"


@pytest.mark.asyncio
@respx.mock
async def test_create_pull_request_422() -> None:
    respx.post(f"{BASE}/repos/o/r/pulls").mock(
        return_value=httpx.Response(422, json={"message": "Validation Failed"})
    )

    async with GitHubClient(token="test") as client:
        with pytest.raises(GitHubValidationError):
            await client.create_pull_request("o", "r", "t", "b", "h", "base")


# ── Retry on 502 ─────────────────────────────────────────────────


@pytest.mark.asyncio
@respx.mock
async def test_get_file_content_retries_on_502() -> None:
    """Verify @github_retry retries on 502 then succeeds."""
    route = respx.get(f"{BASE}/repos/o/r/contents/file.py").mock(
        side_effect=[
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(
                200,
                json={
                    "path": "file.py",
                    "sha": "sha1",
                    "content": "Y29udGVudA==",
                    "encoding": "base64",
                    "size": 7,
                },
            ),
        ]
    )

    async with GitHubClient(token="test") as client:
        fc = await client.get_file_content("o", "r", "file.py")

    assert fc.sha == "sha1"
    assert route.call_count == 2


@pytest.mark.asyncio
@respx.mock
async def test_create_branch_retries_on_502() -> None:
    """Verify create_branch retries on transient 502."""
    route = respx.post(f"{BASE}/repos/o/r/git/refs").mock(
        side_effect=[
            httpx.Response(502, text="Bad Gateway"),
            httpx.Response(
                201,
                json={
                    "ref": "refs/heads/branch",
                    "object": {"sha": "abc"},
                },
            ),
        ]
    )

    async with GitHubClient(token="test") as client:
        ref = await client.create_branch("o", "r", "branch", "abc")

    assert ref.sha == "abc"
    assert route.call_count == 2
