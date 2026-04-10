"""FastAPI webhook ingress for GitHub events."""

from __future__ import annotations

import asyncio
import json
import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any

import structlog
from fastapi import FastAPI, HTTPException, Request, status

from github_auto_maintainer.core.action_policy import ActionPolicy
from github_auto_maintainer.core.idempotency import InMemoryIdempotencyStore
from github_auto_maintainer.core.job_queue import InMemoryJobQueue, JobQueue
from github_auto_maintainer.core.llm_router import LLMRouter
from github_auto_maintainer.core.orchestrator import Orchestrator
from github_auto_maintainer.github.auth import load_private_key_pem
from github_auto_maintainer.github.events import NormalizedEvent, normalize_github_event
from github_auto_maintainer.server.webhooks import (
    InvalidSignatureError,
    MalformedSignatureError,
    MissingHeaderError,
    parse_github_webhook_headers,
    verify_webhook_signature,
)
from github_auto_maintainer.skills.base import BaseSkill
from github_auto_maintainer.skills.issue_label import IssueLabelSkill
from github_auto_maintainer.skills.issue_response import IssueResponseSkill
from github_auto_maintainer.skills.pr_summary import PRSummarySkill


def create_app(
    *,
    queue: JobQueue[NormalizedEvent] | None = None,
    webhook_secret: str | None = None,
    router: LLMRouter | None = None,
) -> FastAPI:
    """Create the webhook ingress app with injectable dependencies."""

    job_queue: JobQueue[NormalizedEvent] = queue or InMemoryJobQueue[NormalizedEvent]()
    configured_secret = webhook_secret

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        logger: structlog.stdlib.BoundLogger = structlog.get_logger()

        dispatcher_task: asyncio.Task[None] | None = None

        app_id = os.getenv("GITHUB_APP_ID", "")
        key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "")

        if app_id and key_path:
            # Agreed Phase 3.5 contract:
            #   - empty/unset path  → warn + skip  (handled by the outer if)
            #   - path set, file missing → warn + skip
            #   - path set, file exists but unreadable → fail loud (OSError propagates)
            key_file = Path(key_path)
            if not key_file.exists():
                logger.warning(
                    "app.dispatcher_skipped",
                    reason="GITHUB_APP_PRIVATE_KEY_PATH file does not exist",
                    path=key_path,
                )
            else:
                # File exists — any read error (permissions, encoding) is a hard failure.
                private_key_pem = load_private_key_pem(key_path)

                llm_router = router or LLMRouter()
                policy = ActionPolicy()
                idempotency_store = InMemoryIdempotencyStore()

                skills: list[BaseSkill] = [
                    PRSummarySkill(),
                    IssueLabelSkill(),
                    IssueResponseSkill(),
                ]

                # Phase 5: Auto-fix skill
                auto_fix_enabled = (
                    os.getenv("AUTO_FIX_ENABLED", "true").strip().lower() != "false"
                )
                if auto_fix_enabled:
                    from github_auto_maintainer.automation.patch_worker import AutoFixSkill
                    from github_auto_maintainer.core.run_store import SQLiteRunStore

                    run_store_path = os.getenv("RUN_STORE_PATH", "runs.db")
                    trigger_label = os.getenv(
                        "AUTO_FIX_TRIGGER_LABEL", "auto-fix"
                    )
                    trigger_command = os.getenv(
                        "AUTO_FIX_TRIGGER_COMMAND", "/auto-fix"
                    )

                    run_store = SQLiteRunStore(db_path=run_store_path)
                    await run_store.initialize()

                    skills.append(
                        AutoFixSkill(
                            run_store=run_store,
                            trigger_label=trigger_label,
                            trigger_command=trigger_command,
                        )
                    )

                orchestrator = Orchestrator(
                    queue=job_queue,
                    skills=skills,
                    router=llm_router,
                    app_id=app_id,
                    private_key_pem=private_key_pem,
                    policy=policy,
                    idempotency_store=idempotency_store,
                    logger=logger,
                )
                dispatcher_task = asyncio.create_task(orchestrator.run())
        else:
            logger.warning(
                "app.dispatcher_skipped",
                reason="GITHUB_APP_ID or GITHUB_APP_PRIVATE_KEY_PATH not set",
            )

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
