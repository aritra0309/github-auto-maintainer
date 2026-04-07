from __future__ import annotations

import httpx
import pytest
import respx

from github_auto_maintainer.github.client import (
    GitHubClient,
    IssueComment,
    PullRequest,
)
from github_auto_maintainer.github.errors import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubRateLimitError,
    GitHubResourceNotFoundError,
    GitHubTransientError,
    GitHubValidationError,
)

BASE = "https://api.github.com"


def _pr_json() -> dict[str, object]:
    return {
        "number": 42,
        "title": "Fix bug",
        "body": "Fixes the bug",
        "state": "open",
        "user": {"login": "author1"},
        "head": {"sha": "abc123", "ref": "fix/bug"},
        "base": {"ref": "main"},
        "labels": [{"name": "bug"}],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


def _issue_json() -> dict[str, object]:
    return {
        "number": 101,
        "title": "Login broken",
        "body": "Cannot login",
        "state": "open",
        "user": {"login": "reporter1"},
        "labels": [{"name": "bug"}],
        "created_at": "2026-01-01T00:00:00Z",
        "updated_at": "2026-01-01T00:00:00Z",
    }


@pytest.mark.asyncio
@respx.mock
async def test_get_pull_request() -> None:
    route = respx.get(f"{BASE}/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(200, json=_pr_json())
    )
    async with GitHubClient(token="test-token") as client:
        pr = await client.get_pull_request("owner", "repo", 42)

    assert route.called
    assert isinstance(pr, PullRequest)
    assert pr.number == 42
    assert pr.title == "Fix bug"
    assert pr.author == "author1"
    assert pr.labels == ("bug",)


@pytest.mark.asyncio
@respx.mock
async def test_get_pull_request_diff() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/42").mock(
        return_value=httpx.Response(200, text="diff --git a/f b/f\n+hello\n")
    )
    async with GitHubClient(token="test-token") as client:
        diff = await client.get_pull_request_diff("owner", "repo", 42)

    assert "diff --git" in diff


@pytest.mark.asyncio
@respx.mock
async def test_get_pull_request_files_with_pagination() -> None:
    page1_url = f"{BASE}/repos/owner/repo/pulls/42/files"
    page2_url = f"{BASE}/repos/owner/repo/pulls/42/files?page=2"

    respx.get(url__eq=page1_url).mock(
        return_value=httpx.Response(
            200,
            json=[{"filename": "a.py", "status": "modified", "additions": 1, "deletions": 0, "patch": "+x"}],
            headers={"link": f'<{page2_url}>; rel="next"'},
        )
    )
    respx.get(url__eq=page2_url).mock(
        return_value=httpx.Response(
            200,
            json=[{"filename": "b.py", "status": "added", "additions": 2, "deletions": 0, "patch": "+y"}],
        )
    )

    async with GitHubClient(token="test-token") as client:
        files = await client.get_pull_request_files("owner", "repo", 42)

    assert len(files) == 2
    assert files[0].filename == "a.py"
    assert files[1].filename == "b.py"


@pytest.mark.asyncio
@respx.mock
async def test_get_issue() -> None:
    respx.get(f"{BASE}/repos/owner/repo/issues/101").mock(
        return_value=httpx.Response(200, json=_issue_json())
    )
    async with GitHubClient(token="test-token") as client:
        issue = await client.get_issue("owner", "repo", 101)

    assert issue.number == 101
    assert issue.author == "reporter1"


@pytest.mark.asyncio
@respx.mock
async def test_get_issue_comments() -> None:
    comments = [
        {"id": 1, "user": {"login": "user1"}, "body": "comment 1", "created_at": "2026-01-01T00:00:00Z"},
        {"id": 2, "user": {"login": "user2"}, "body": "comment 2", "created_at": "2026-01-01T01:00:00Z"},
    ]
    respx.get(f"{BASE}/repos/owner/repo/issues/101/comments").mock(
        return_value=httpx.Response(200, json=comments)
    )
    async with GitHubClient(token="test-token") as client:
        result = await client.get_issue_comments("owner", "repo", 101)

    assert len(result) == 2
    assert isinstance(result[0], IssueComment)
    assert result[0].author == "user1"


@pytest.mark.asyncio
@respx.mock
async def test_error_401_raises_authentication_error() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(401, text="Unauthorized")
    )
    async with GitHubClient(token="bad-token") as client:
        with pytest.raises(GitHubAuthenticationError):
            await client.get_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
@respx.mock
async def test_error_403_rate_limit() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(
            403,
            text="Rate limited",
            headers={"x-ratelimit-remaining": "0"},
        )
    )
    async with GitHubClient(token="token") as client:
        with pytest.raises(GitHubRateLimitError):
            await client.get_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
@respx.mock
async def test_error_403_non_rate_limit() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(403, text="Forbidden")
    )
    async with GitHubClient(token="token") as client:
        with pytest.raises(GitHubClientError):
            await client.get_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
@respx.mock
async def test_error_404_raises_not_found() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/999").mock(
        return_value=httpx.Response(404, text="Not Found")
    )
    async with GitHubClient(token="token") as client:
        with pytest.raises(GitHubResourceNotFoundError):
            await client.get_pull_request("owner", "repo", 999)


@pytest.mark.asyncio
@respx.mock
async def test_error_422_raises_validation_error() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(422, text="Unprocessable")
    )
    async with GitHubClient(token="token") as client:
        with pytest.raises(GitHubValidationError):
            await client.get_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
@respx.mock
async def test_error_502_raises_transient_error() -> None:
    respx.get(f"{BASE}/repos/owner/repo/pulls/1").mock(
        return_value=httpx.Response(502, text="Bad Gateway")
    )
    async with GitHubClient(token="token") as client:
        with pytest.raises(GitHubTransientError):
            await client.get_pull_request("owner", "repo", 1)


@pytest.mark.asyncio
@respx.mock
async def test_headers_include_auth_and_version() -> None:
    route = respx.get(f"{BASE}/repos/o/r/pulls/1").mock(
        return_value=httpx.Response(200, json=_pr_json())
    )
    async with GitHubClient(token="my-token") as client:
        await client.get_pull_request("o", "r", 1)

    assert route.calls[0].request.headers["authorization"] == "Bearer my-token"
    assert route.calls[0].request.headers["x-github-api-version"] == "2022-11-28"


@pytest.mark.asyncio
async def test_client_outside_context_manager_raises() -> None:
    client = GitHubClient(token="token")
    with pytest.raises(RuntimeError, match="async context manager"):
        await client.get_pull_request("o", "r", 1)
