"""FastAPI webhook ingress for GitHub events."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status

from github_auto_maintainer.core.job_queue import InMemoryJobQueue, JobQueue
from github_auto_maintainer.core.llm_router import LLMRouter
from github_auto_maintainer.core.skill_dispatcher import SkillDispatcher
from github_auto_maintainer.github.events import NormalizedEvent, normalize_github_event
from github_auto_maintainer.server.webhooks import (
    InvalidSignatureError,
    MalformedSignatureError,
    MissingHeaderError,
    parse_github_webhook_headers,
    verify_webhook_signature,
)
from github_auto_maintainer.skills.issue_triage import IssueTriageSkill
from github_auto_maintainer.skills.pr_triage import PRTriageSkill


def create_app(
    *,
    queue: JobQueue[NormalizedEvent] | None = None,
    webhook_secret: str | None = None,
) -> FastAPI:
    """Create the webhook ingress app with injectable dependencies."""

    job_queue: JobQueue[NormalizedEvent] = queue or InMemoryJobQueue[NormalizedEvent]()
    configured_secret = webhook_secret

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        app_id = os.getenv("GITHUB_APP_ID", "")
        private_key_pem = os.getenv("GITHUB_APP_PRIVATE_KEY", "")
        logger: structlog.stdlib.BoundLogger = structlog.get_logger()

        dispatcher_task: asyncio.Task[None] | None = None
        if app_id and private_key_pem:
            dispatcher = SkillDispatcher(
                queue=job_queue,
                skills=[PRTriageSkill(), IssueTriageSkill()],
                router=LLMRouter(),
                app_id=app_id,
                private_key_pem=private_key_pem,
                logger=logger,
            )
            dispatcher_task = asyncio.create_task(dispatcher.run())

        yield

        if dispatcher_task is not None:
            dispatcher_task.cancel()
            try:
                await dispatcher_task
            except asyncio.CancelledError:
                pass

    app = FastAPI(title="github-auto-maintainer-webhook-ingress", lifespan=lifespan)

    @app.get("/health")
    async def health() -> dict[str, str]:
        return {"status": "ok"}

    @app.post("/webhook", status_code=status.HTTP_202_ACCEPTED)
    async def webhook(request: Request) -> dict[str, str]:
        try:
            headers = parse_github_webhook_headers(request.headers)
        except MissingHeaderError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except MalformedSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc

        body = await request.body()
        secret = _resolve_webhook_secret(configured_secret=configured_secret)

        try:
            verify_webhook_signature(
                secret=secret,
                body=body,
                signature_header=headers.signature,
            )
        except MissingHeaderError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except MalformedSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_400_BAD_REQUEST,
                detail=str(exc),
            ) from exc
        except InvalidSignatureError as exc:
            raise HTTPException(
                status_code=status.HTTP_401_UNAUTHORIZED,
                detail=str(exc),
            ) from exc

        payload = _parse_payload(body)
        event = normalize_github_event(
            github_event=headers.github_event,
            delivery_id=headers.delivery_id,
            payload=payload,
        )
        await job_queue.enqueue(event)
        return {"status": "accepted"}

    return app


def _resolve_webhook_secret(*, configured_secret: str | None) -> str:
    resolved_secret = configured_secret or os.getenv("GITHUB_WEBHOOK_SECRET")
    if resolved_secret is None or not resolved_secret.strip():
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="GITHUB_WEBHOOK_SECRET is not configured",
        )
    return resolved_secret


def _parse_payload(body: bytes) -> dict[str, Any]:
    try:
        data = json.loads(body.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook body must be valid UTF-8 JSON",
        ) from exc
    if not isinstance(data, dict):
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="Webhook JSON payload must be an object",
        )
    payload: dict[str, Any] = {}
    for key, value in data.items():
        payload[str(key)] = value
    return payload


app = create_app()
