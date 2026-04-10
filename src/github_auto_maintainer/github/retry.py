"""Reusable retry helper for transient GitHub API failures."""

from __future__ import annotations

from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_exponential

from github_auto_maintainer.github.errors import GitHubTransientError

github_retry = retry(
    retry=retry_if_exception_type(GitHubTransientError),
    stop=stop_after_attempt(3),
    wait=wait_exponential(multiplier=0.5, max=4),
    reraise=True,
)
