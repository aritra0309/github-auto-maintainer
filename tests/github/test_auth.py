from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import httpx
import jwt
import pytest

from github_auto_maintainer.github.auth import (
    InstallationTokenError,
    fetch_installation_access_token,
    fetch_repository_installation,
    generate_github_app_jwt,
    load_private_key_pem,
)

FIXTURE_PRIVATE_KEY_PATH = (
    Path(__file__).resolve().parents[1] / "fixtures" / "github_app_test_private_key.pem"
)


def test_generate_github_app_jwt_contains_expected_claims() -> None:
    private_key_pem = load_private_key_pem(FIXTURE_PRIVATE_KEY_PATH)
    token = generate_github_app_jwt(app_id="12345", private_key_pem=private_key_pem)

    decoded = jwt.decode(
        token,
        options={"verify_signature": False, "verify_exp": False, "verify_iat": False},
        algorithms=["RS256"],
    )
    now = int(datetime.now(tz=UTC).timestamp())

    assert decoded["iss"] == "12345"
    assert isinstance(decoded["iat"], int)
    assert isinstance(decoded["exp"], int)
    assert decoded["exp"] > decoded["iat"]
    assert decoded["exp"] - decoded["iat"] <= 10 * 60
    assert decoded["iat"] <= now


@pytest.mark.asyncio
async def test_fetch_installation_access_token_returns_token_from_github_response() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/app/installations/42/access_tokens"
        assert request.headers["authorization"] == "Bearer app-jwt"
        return httpx.Response(
            status_code=201,
            json={"token": "inst-token", "expires_at": "2026-04-06T13:00:00Z"},
        )

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        token = await fetch_installation_access_token(
            app_jwt="app-jwt",
            installation_id=42,
            client=client,
        )

    assert token.token == "inst-token"
    assert token.expires_at == "2026-04-06T13:00:00Z"


@pytest.mark.asyncio
async def test_fetch_installation_access_token_raises_on_failure_status() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        _ = request
        return httpx.Response(status_code=401, text="bad credentials")

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        with pytest.raises(InstallationTokenError, match="status=401"):
            await fetch_installation_access_token(
                app_jwt="bad-jwt",
                installation_id=42,
                client=client,
            )


@pytest.mark.asyncio
async def test_fetch_repository_installation_returns_installation_id() -> None:
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/repos/octo/repo/installation"
        return httpx.Response(status_code=200, json={"id": 777})

    transport = httpx.MockTransport(handler)
    async with httpx.AsyncClient(transport=transport, base_url="https://api.github.com") as client:
        installation = await fetch_repository_installation(
            app_jwt="app-jwt",
            owner="octo",
            repo="repo",
            client=client,
        )

    assert installation.installation_id == 777
