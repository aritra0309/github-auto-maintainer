"""GitHub App authentication helpers (JWT and installation tokens)."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx
import jwt

from github_auto_maintainer.github.errors import GitHubTransientError
from github_auto_maintainer.github.retry import github_retry


class GitHubAuthError(Exception):
    """Base error for GitHub App authentication failures."""


class InstallationTokenError(GitHubAuthError):
    """Raised when installation token retrieval fails."""


@dataclass(frozen=True, slots=True)
class InstallationAccessToken:
    """Installation token payload used by downstream GitHub API calls."""

    token: str
    expires_at: str


@dataclass(frozen=True, slots=True)
class RepositoryInstallation:
    """GitHub App installation metadata for a repository."""

    installation_id: int


def load_private_key_pem(path: str | Path) -> str:
    """Load a PEM-formatted private key from disk."""

    private_key_path = Path(path)
    return private_key_path.read_text(encoding="utf-8")


def generate_github_app_jwt(*, app_id: str | int, private_key_pem: str) -> str:
    """Generate a GitHub App bearer JWT signed with RS256."""

    now = datetime.now(tz=UTC)
    payload = {
        "iat": int((now - timedelta(seconds=60)).timestamp()),
        "exp": int((now + timedelta(minutes=9)).timestamp()),
        "iss": str(app_id),
    }
    token: Any = jwt.encode(payload, private_key_pem, algorithm="RS256")
    if isinstance(token, str):
        return token
    if isinstance(token, bytes):
        return token.decode("utf-8")
    raise GitHubAuthError("JWT encoder returned unexpected token type")


@github_retry
async def fetch_installation_access_token(
    *,
    app_jwt: str,
    installation_id: int,
    base_url: str = "https://api.github.com",
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> InstallationAccessToken:
    """Create an installation access token for a GitHub App installation."""

    endpoint = f"{base_url.rstrip('/')}/app/installations/{installation_id}/access_tokens"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {app_jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if client is not None:
        response = await client.post(endpoint, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=timeout_seconds) as async_client:
            response = await async_client.post(endpoint, headers=headers)

    if response.status_code in (502, 503, 504):
        raise GitHubTransientError(
            f"GitHub installation token request failed (transient): "
            f"status={response.status_code} body={response.text}",
            status_code=response.status_code,
            response_body=response.text,
        )

    if response.status_code >= 400:
        raise InstallationTokenError(
            "GitHub installation token request failed: "
            f"status={response.status_code} body={response.text}"
        )

    payload = _response_json(response)
    token = payload.get("token")
    expires_at = payload.get("expires_at")
    if not isinstance(token, str) or not token:
        raise InstallationTokenError("GitHub installation token response missing token")
    if not isinstance(expires_at, str) or not expires_at:
        raise InstallationTokenError("GitHub installation token response missing expires_at")

    return InstallationAccessToken(token=token, expires_at=expires_at)


async def fetch_repository_installation(
    *,
    app_jwt: str,
    owner: str,
    repo: str,
    base_url: str = "https://api.github.com",
    timeout_seconds: float = 10.0,
    client: httpx.AsyncClient | None = None,
) -> RepositoryInstallation:
    """Fetch the GitHub App installation metadata for a repository."""

    endpoint = f"{base_url.rstrip('/')}/repos/{owner}/{repo}/installation"
    headers = {
        "Accept": "application/vnd.github+json",
        "Authorization": f"Bearer {app_jwt}",
        "X-GitHub-Api-Version": "2022-11-28",
    }

    if client is not None:
        response = await client.get(endpoint, headers=headers)
    else:
        async with httpx.AsyncClient(timeout=timeout_seconds) as async_client:
            response = await async_client.get(endpoint, headers=headers)

    if response.status_code >= 400:
        raise InstallationTokenError(
            "GitHub installation lookup failed: "
            f"status={response.status_code} body={response.text}"
        )

    payload = _response_json(response)
    installation_id = payload.get("id")
    if not isinstance(installation_id, int):
        raise InstallationTokenError("GitHub installation lookup response missing id")

    return RepositoryInstallation(installation_id=installation_id)


def _response_json(response: httpx.Response) -> dict[str, Any]:
    try:
        data = response.json()
    except ValueError as exc:
        raise InstallationTokenError(
            "GitHub installation token response was not valid JSON"
        ) from exc
    if not isinstance(data, dict):
        raise InstallationTokenError("GitHub installation token response JSON must be an object")
    return data
