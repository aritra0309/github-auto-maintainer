"""Async GitHub REST API client with read and write operations."""

from __future__ import annotations

import re
from dataclasses import dataclass
from types import TracebackType
from typing import Any, Self

import httpx

from github_auto_maintainer.github.errors import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubRateLimitError,
    GitHubResourceNotFoundError,
    GitHubTransientError,
    GitHubValidationError,
)

_LINK_NEXT_RE = re.compile(r'<([^>]+)>;\s*rel="next"')

_MAX_PAGES = 10
_MAX_ITEMS = 300

_JSON_ACCEPT = "application/vnd.github+json"
_DIFF_ACCEPT = "application/vnd.github.diff"
_API_VERSION = "2022-11-28"


@dataclass(frozen=True, slots=True)
class PullRequest:
    """GitHub pull request metadata."""

    number: int
    title: str
    body: str
    state: str
    head_sha: str
    base_ref: str
    head_ref: str
    author: str
    labels: tuple[str, ...]
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class PullRequestFile:
    """Single file entry from the pull request files endpoint."""

    filename: str
    status: str
    additions: int
    deletions: int
    patch: str


@dataclass(frozen=True, slots=True)
class Issue:
    """GitHub issue metadata."""

    number: int
    title: str
    body: str
    state: str
    labels: tuple[str, ...]
    author: str
    created_at: str
    updated_at: str


@dataclass(frozen=True, slots=True)
class IssueComment:
    """Single comment on a GitHub issue."""

    id: int
    author: str
    body: str
    created_at: str


@dataclass(frozen=True, slots=True)
class PullRequestReview:
    """A pull request review."""

    id: int
    state: str
    body: str


class GitHubClient:
    """Async GitHub REST client with read and write operations."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.github.com",
        timeout: float = 30.0,
    ) -> None:
        self._token = token
        self._base_url = base_url.rstrip("/")
        self._timeout = timeout
        self._client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> Self:
        self._client = httpx.AsyncClient(timeout=self._timeout)
        return self

    async def __aexit__(
        self,
        exc_type: type[BaseException] | None,
        exc_val: BaseException | None,
        exc_tb: TracebackType | None,
    ) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    # ── Read API ──────────────────────────────────────────────

    async def get_pull_request(self, owner: str, repo: str, number: int) -> PullRequest:
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls/{number}"
        data = await self._get_json(url)
        return _parse_pull_request(data)

    async def get_pull_request_diff(self, owner: str, repo: str, number: int) -> str:
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls/{number}"
        return await self._get_text(url, accept=_DIFF_ACCEPT)

    async def get_pull_request_files(
        self, owner: str, repo: str, number: int
    ) -> tuple[PullRequestFile, ...]:
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls/{number}/files"
        items = await self._get_paginated(url)
        return tuple(_parse_pull_request_file(item) for item in items)

    async def get_issue(self, owner: str, repo: str, number: int) -> Issue:
        url = f"{self._base_url}/repos/{owner}/{repo}/issues/{number}"
        data = await self._get_json(url)
        return _parse_issue(data)

    async def get_issue_comments(
        self, owner: str, repo: str, number: int
    ) -> tuple[IssueComment, ...]:
        url = f"{self._base_url}/repos/{owner}/{repo}/issues/{number}/comments"
        items = await self._get_paginated(url)
        return tuple(_parse_issue_comment(item) for item in items)

    # ── Write API ─────────────────────────────────────────────

    async def create_issue_comment(
        self, owner: str, repo: str, issue_number: int, body: str
    ) -> IssueComment:
        """Create a comment on an issue or pull request."""
        url = f"{self._base_url}/repos/{owner}/{repo}/issues/{issue_number}/comments"
        data = await self._post_json(url, {"body": body})
        return _parse_issue_comment(data)

    async def add_labels(
        self, owner: str, repo: str, issue_number: int, labels: tuple[str, ...]
    ) -> tuple[str, ...]:
        """Add labels to an issue. Returns all current labels on the issue."""
        url = f"{self._base_url}/repos/{owner}/{repo}/issues/{issue_number}/labels"
        client = self._ensure_client()
        response = await client.post(
            url, json={"labels": list(labels)}, headers=self._json_headers()
        )
        _raise_for_status(response)
        raw: Any = response.json()
        if not isinstance(raw, list):
            msg = f"Expected JSON array from {url}"
            raise GitHubClientError(msg, status_code=response.status_code)
        label_names: list[str] = []
        for item in raw:
            if isinstance(item, dict):
                label_names.append(str(item.get("name", "")))
        return tuple(label_names)

    async def create_pr_review_summary(
        self,
        owner: str,
        repo: str,
        pr_number: int,
        body: str,
        *,
        event: str = "COMMENT",
        commit_id: str | None = None,
    ) -> PullRequestReview:
        """Create a pull request review (summary only, no line comments)."""
        url = f"{self._base_url}/repos/{owner}/{repo}/pulls/{pr_number}/reviews"
        payload: dict[str, Any] = {"body": body, "event": event}
        if commit_id is not None:
            payload["commit_id"] = commit_id
        data = await self._post_json(url, payload)
        return PullRequestReview(
            id=int(data.get("id", 0)),
            state=str(data.get("state", "")),
            body=str(data.get("body") or ""),
        )

    # ── Internal helpers ──────────────────────────────────────

    def _ensure_client(self) -> httpx.AsyncClient:
        if self._client is None:
            msg = "GitHubClient must be used as an async context manager"
            raise RuntimeError(msg)
        return self._client

    def _json_headers(self) -> dict[str, str]:
        return {
            "Accept": _JSON_ACCEPT,
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    def _diff_headers(self) -> dict[str, str]:
        return {
            "Accept": _DIFF_ACCEPT,
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": _API_VERSION,
        }

    async def _get_json(self, url: str) -> dict[str, Any]:
        client = self._ensure_client()
        response = await client.get(url, headers=self._json_headers())
        _raise_for_status(response)
        data: Any = response.json()
        if not isinstance(data, dict):
            msg = f"Expected JSON object from {url}"
            raise GitHubClientError(msg, status_code=response.status_code)
        return data

    async def _post_json(self, url: str, body: dict[str, Any]) -> dict[str, Any]:
        client = self._ensure_client()
        response = await client.post(url, json=body, headers=self._json_headers())
        _raise_for_status(response)
        data: Any = response.json()
        if not isinstance(data, dict):
            msg = f"Expected JSON object from {url}"
            raise GitHubClientError(msg, status_code=response.status_code)
        return data

    async def _get_text(self, url: str, *, accept: str) -> str:
        client = self._ensure_client()
        headers = {
            "Accept": accept,
            "Authorization": f"Bearer {self._token}",
            "X-GitHub-Api-Version": _API_VERSION,
        }
        response = await client.get(url, headers=headers)
        _raise_for_status(response)
        return response.text

    async def _get_paginated(self, url: str) -> list[dict[str, Any]]:
        client = self._ensure_client()
        items: list[dict[str, Any]] = []
        current_url: str | None = url
        pages = 0

        while current_url is not None and pages < _MAX_PAGES and len(items) < _MAX_ITEMS:
            response = await client.get(current_url, headers=self._json_headers())
            _raise_for_status(response)
            data: Any = response.json()
            if not isinstance(data, list):
                break
            for item in data:
                if isinstance(item, dict) and len(items) < _MAX_ITEMS:
                    items.append(item)
            pages += 1
            current_url = _parse_next_link(response.headers.get("link", ""))

        return items


def _raise_for_status(response: httpx.Response) -> None:
    code = response.status_code
    if code < 400:
        return

    body = response.text
    msg = f"GitHub API error: status={code} body={body}"

    if code == 401:
        raise GitHubAuthenticationError(msg, status_code=code, response_body=body)
    if code == 403:
        remaining = response.headers.get("x-ratelimit-remaining", "")
        if remaining == "0":
            raise GitHubRateLimitError(msg, status_code=code, response_body=body)
        raise GitHubClientError(msg, status_code=code, response_body=body)
    if code == 404:
        raise GitHubResourceNotFoundError(msg, status_code=code, response_body=body)
    if code == 422:
        raise GitHubValidationError(msg, status_code=code, response_body=body)
    if code in (502, 503, 504):
        raise GitHubTransientError(msg, status_code=code, response_body=body)

    raise GitHubClientError(msg, status_code=code, response_body=body)


def _parse_next_link(link_header: str) -> str | None:
    match = _LINK_NEXT_RE.search(link_header)
    return match.group(1) if match else None


# ── Response parsers ──────────────────────────────────────────────────────────


def _parse_pull_request(data: dict[str, Any]) -> PullRequest:
    labels_raw = data.get("labels") or []
    labels = tuple(
        label.get("name", "") for label in labels_raw if isinstance(label, dict)
    )
    user = data.get("user") or {}
    head = data.get("head") or {}
    base = data.get("base") or {}
    return PullRequest(
        number=int(data.get("number", 0)),
        title=str(data.get("title", "")),
        body=str(data.get("body") or ""),
        state=str(data.get("state", "")),
        head_sha=str(head.get("sha", "")),
        base_ref=str(base.get("ref", "")),
        head_ref=str(head.get("ref", "")),
        author=str(user.get("login", "")),
        labels=labels,
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def _parse_pull_request_file(data: dict[str, Any]) -> PullRequestFile:
    return PullRequestFile(
        filename=str(data.get("filename", "")),
        status=str(data.get("status", "")),
        additions=int(data.get("additions", 0)),
        deletions=int(data.get("deletions", 0)),
        patch=str(data.get("patch") or ""),
    )


def _parse_issue(data: dict[str, Any]) -> Issue:
    labels_raw = data.get("labels") or []
    labels = tuple(
        label.get("name", "") for label in labels_raw if isinstance(label, dict)
    )
    user = data.get("user") or {}
    return Issue(
        number=int(data.get("number", 0)),
        title=str(data.get("title", "")),
        body=str(data.get("body") or ""),
        state=str(data.get("state", "")),
        labels=labels,
        author=str(user.get("login", "")),
        created_at=str(data.get("created_at", "")),
        updated_at=str(data.get("updated_at", "")),
    )


def _parse_issue_comment(data: dict[str, Any]) -> IssueComment:
    user = data.get("user") or {}
    return IssueComment(
        id=int(data.get("id", 0)),
        author=str(user.get("login", "")),
        body=str(data.get("body") or ""),
        created_at=str(data.get("created_at", "")),
    )
