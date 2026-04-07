"""GitHub REST API client error hierarchy."""

from __future__ import annotations


class GitHubClientError(Exception):
    """Base error for GitHub API client failures."""

    def __init__(self, message: str, status_code: int = 0, response_body: str = "") -> None:
        self.status_code = status_code
        self.response_body = response_body
        super().__init__(message)


class GitHubAuthenticationError(GitHubClientError):
    """Raised on 401 authentication failures."""


class GitHubResourceNotFoundError(GitHubClientError):
    """Raised on 404 responses."""


class GitHubValidationError(GitHubClientError):
    """Raised on 422 validation failures."""


class GitHubRateLimitError(GitHubClientError):
    """Raised when rate limit is exhausted (403 with X-RateLimit-Remaining: 0)."""


class GitHubTransientError(GitHubClientError):
    """Raised on transient server errors (502, 503, 504)."""
