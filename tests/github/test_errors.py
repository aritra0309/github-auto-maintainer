from __future__ import annotations

from github_auto_maintainer.github.errors import (
    GitHubAuthenticationError,
    GitHubClientError,
    GitHubRateLimitError,
    GitHubResourceNotFoundError,
    GitHubTransientError,
    GitHubValidationError,
)


def test_github_client_error_stores_status_code_and_body() -> None:
    err = GitHubClientError("fail", status_code=500, response_body="oops")
    assert err.status_code == 500
    assert err.response_body == "oops"
    assert str(err) == "fail"


def test_github_client_error_defaults() -> None:
    err = GitHubClientError("fail")
    assert err.status_code == 0
    assert err.response_body == ""


def test_authentication_error_is_client_error() -> None:
    err = GitHubAuthenticationError("auth", status_code=401)
    assert isinstance(err, GitHubClientError)
    assert err.status_code == 401


def test_resource_not_found_error_is_client_error() -> None:
    err = GitHubResourceNotFoundError("nope", status_code=404)
    assert isinstance(err, GitHubClientError)


def test_validation_error_is_client_error() -> None:
    err = GitHubValidationError("bad", status_code=422)
    assert isinstance(err, GitHubClientError)


def test_rate_limit_error_is_client_error() -> None:
    err = GitHubRateLimitError("slow down", status_code=403)
    assert isinstance(err, GitHubClientError)


def test_transient_error_is_client_error() -> None:
    err = GitHubTransientError("retry", status_code=502)
    assert isinstance(err, GitHubClientError)


def test_github_errors_are_not_llm_router_errors() -> None:
    """GitHub errors must be a separate hierarchy from LLM router errors."""
    from github_auto_maintainer.core.errors import LLMRouterError

    err = GitHubClientError("test")
    assert not isinstance(err, LLMRouterError)
