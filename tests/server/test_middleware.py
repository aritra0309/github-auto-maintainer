"""Tests for request timing middleware."""

from __future__ import annotations

from fastapi import FastAPI
from fastapi.testclient import TestClient

from github_auto_maintainer.server.middleware import RequestTimingMiddleware


def _create_test_app() -> FastAPI:
    """Create a minimal FastAPI app with the timing middleware."""
    app = FastAPI()
    app.add_middleware(RequestTimingMiddleware)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook")
    async def webhook() -> dict[str, str]:
        return {"status": "accepted"}

    return app


class TestRequestTimingMiddleware:
    """Tests for RequestTimingMiddleware."""

    def test_health_request_returns_request_id(self) -> None:
        """Health requests should include X-Request-ID in response."""
        app = _create_test_app()
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
        # Verify it's a UUID-like string
        request_id = response.headers["X-Request-ID"]
        assert len(request_id) == 36  # UUID4 format

    def test_webhook_with_delivery_id(self) -> None:
        """Webhook requests with X-GitHub-Delivery should be handled."""
        app = _create_test_app()
        client = TestClient(app)
        response = client.post(
            "/webhook",
            headers={"X-GitHub-Delivery": "test-delivery-123"},
        )
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers

    def test_request_without_delivery_id(self) -> None:
        """Requests without X-GitHub-Delivery should still work."""
        app = _create_test_app()
        client = TestClient(app)
        response = client.get("/health")
        assert response.status_code == 200
        assert "X-Request-ID" in response.headers
