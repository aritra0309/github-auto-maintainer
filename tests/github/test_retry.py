"""Tests for the GitHub retry helper."""

from __future__ import annotations

import pytest
from tenacity import retry, retry_if_exception_type, stop_after_attempt, wait_none

from github_auto_maintainer.github.errors import (
    GitHubAuthenticationError,
    GitHubConflictError,
    GitHubResourceNotFoundError,
    GitHubTransientError,
    GitHubValidationError,
)

# Build a test-specific retry decorator with wait_none() to avoid real delays.
_test_retry = retry(
    retry=retry_if_exception_type(GitHubTransientError),
    stop=stop_after_attempt(3),
    wait=wait_none(),
    reraise=True,
)


@pytest.mark.asyncio
async def test_retry_succeeds_after_transient_failure() -> None:
    """A function that raises GitHubTransientError once then succeeds is retried."""
    call_count = 0

    @_test_retry
    async def flaky() -> str:
        nonlocal call_count
        call_count += 1
        if call_count == 1:
            raise GitHubTransientError("transient", status_code=502)
        return "ok"

    result = await flaky()
    assert result == "ok"
    assert call_count == 2


@pytest.mark.asyncio
async def test_retry_does_not_retry_conflict_error() -> None:
    """GitHubConflictError fails immediately without retry."""
    call_count = 0

    @_test_retry
    async def conflict() -> str:
        nonlocal call_count
        call_count += 1
        raise GitHubConflictError("conflict", status_code=409)

    with pytest.raises(GitHubConflictError):
        await conflict()
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_does_not_retry_authentication_error() -> None:
    """GitHubAuthenticationError fails immediately."""
    call_count = 0

    @_test_retry
    async def auth_fail() -> str:
        nonlocal call_count
        call_count += 1
        raise GitHubAuthenticationError("auth", status_code=401)

    with pytest.raises(GitHubAuthenticationError):
        await auth_fail()
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_does_not_retry_not_found_error() -> None:
    """GitHubResourceNotFoundError fails immediately."""
    call_count = 0

    @_test_retry
    async def not_found() -> str:
        nonlocal call_count
        call_count += 1
        raise GitHubResourceNotFoundError("not found", status_code=404)

    with pytest.raises(GitHubResourceNotFoundError):
        await not_found()
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_does_not_retry_validation_error() -> None:
    """GitHubValidationError fails immediately."""
    call_count = 0

    @_test_retry
    async def validation_fail() -> str:
        nonlocal call_count
        call_count += 1
        raise GitHubValidationError("bad", status_code=422)

    with pytest.raises(GitHubValidationError):
        await validation_fail()
    assert call_count == 1


@pytest.mark.asyncio
async def test_retry_exhaustion_reraises_transient_error() -> None:
    """Exhausted retries surface the original GitHubTransientError, not RetryError."""
    call_count = 0

    @_test_retry
    async def always_fail() -> str:
        nonlocal call_count
        call_count += 1
        raise GitHubTransientError("always transient", status_code=503)

    with pytest.raises(GitHubTransientError, match="always transient"):
        await always_fail()
    assert call_count == 3
