"""Request-level timing and observability middleware."""

from __future__ import annotations

import time
import uuid
from collections.abc import Awaitable, Callable

import structlog
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response


class RequestTimingMiddleware(BaseHTTPMiddleware):
    """Log request latency and bind a request_id to structlog contextvars."""

    async def dispatch(
        self,
        request: Request,
        call_next: Callable[[Request], Awaitable[Response]],
    ) -> Response:
        request_id = str(uuid.uuid4())
        delivery_id = request.headers.get("X-GitHub-Delivery", "")

        structlog.contextvars.clear_contextvars()
        structlog.contextvars.bind_contextvars(
            request_id=request_id,
            delivery_id=delivery_id or None,
        )

        logger: structlog.stdlib.BoundLogger = structlog.get_logger("http")
        start = time.monotonic()

        response = await call_next(request)

        latency_ms = round((time.monotonic() - start) * 1000, 2)
        logger.info(
            "http.request_completed",
            method=request.method,
            path=request.url.path,
            status_code=response.status_code,
            latency_ms=latency_ms,
        )

        response.headers["X-Request-ID"] = request_id
        return response
