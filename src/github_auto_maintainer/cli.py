"""Single-shot CLI mode for processing a single GitHub event.

Used by the GitHub Actions workflow to process events without a persistent
server — reads the event JSON from a file, runs it through the orchestrator,
and exits.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import uuid
from pathlib import Path
from typing import Any

import structlog

from github_auto_maintainer.core.action_policy import ActionPolicy
from github_auto_maintainer.core.hook_subscribers import LoggingHookSubscriber
from github_auto_maintainer.core.idempotency import InMemoryIdempotencyStore
from github_auto_maintainer.core.job_queue import InMemoryJobQueue
from github_auto_maintainer.core.llm_router import LLMRouter
from github_auto_maintainer.core.logging_config import configure_logging
from github_auto_maintainer.core.model_catalog import ModelCatalog
from github_auto_maintainer.core.orchestrator import Orchestrator
from github_auto_maintainer.github.auth import load_private_key_pem
from github_auto_maintainer.github.events import NormalizedEvent, normalize_github_event
from github_auto_maintainer.skills.base import BaseSkill
from github_auto_maintainer.skills.issue_label import IssueLabelSkill
from github_auto_maintainer.skills.issue_response import IssueResponseSkill
from github_auto_maintainer.skills.pr_summary import PRSummarySkill


def _load_event_payload(event_path: str) -> dict[str, Any]:
    """Read and parse the event JSON file."""
    path = Path(event_path)
    if not path.exists():
        print(f"Event file not found: {event_path}", file=sys.stderr)
        raise SystemExit(1)

    raw = path.read_text(encoding="utf-8")
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        print(f"Invalid JSON in event file: {exc}", file=sys.stderr)
        raise SystemExit(1) from exc

    if not isinstance(data, dict):
        print("Event JSON must be an object", file=sys.stderr)
        raise SystemExit(1)

    payload: dict[str, Any] = {}
    for key, value in data.items():
        payload[str(key)] = value
    return payload


def _resolve_github_event(event_name_env: str | None) -> str:
    """Resolve the GitHub event type from environment.

    In GitHub Actions, ``GITHUB_EVENT_NAME`` provides the event type
    (e.g. ``issues``, ``pull_request``, ``issue_comment``).
    """
    if event_name_env:
        return event_name_env.strip().lower()
    print(
        "GITHUB_EVENT_NAME is not set. "
        "Set it to the event type (e.g. 'issues', 'pull_request').",
        file=sys.stderr,
    )
    raise SystemExit(1)


async def _run_single_event(
    event: NormalizedEvent,
    router: LLMRouter,
    app_id: str,
    private_key_pem: str,
    logger: structlog.stdlib.BoundLogger,
) -> None:
    """Process a single event through the orchestrator and return."""
    queue: InMemoryJobQueue[NormalizedEvent] = InMemoryJobQueue()
    await queue.enqueue(event)

    policy = ActionPolicy()
    idempotency_store = InMemoryIdempotencyStore()

    skills: list[BaseSkill] = [
        PRSummarySkill(),
        IssueLabelSkill(),
        IssueResponseSkill(),
    ]

    # Phase 5: Auto-fix skill (conditional)
    auto_fix_enabled = os.getenv("AUTO_FIX_ENABLED", "true").strip().lower() != "false"
    if auto_fix_enabled:
        from github_auto_maintainer.automation.patch_worker import AutoFixSkill
        from github_auto_maintainer.core.run_store import SQLiteRunStore

        run_store_path = os.getenv("RUN_STORE_PATH", "runs.db")
        trigger_label = os.getenv("AUTO_FIX_TRIGGER_LABEL", "auto-fix")
        trigger_command = os.getenv("AUTO_FIX_TRIGGER_COMMAND", "/auto-fix")

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
        queue=queue,
        skills=skills,
        router=router,
        app_id=app_id,
        private_key_pem=private_key_pem,
        policy=policy,
        idempotency_store=idempotency_store,
        logger=logger,
    )

    # Process the single event directly instead of running the infinite loop.
    await orchestrator._process_event(event)

    logger.info(
        "cli.event_processed",
        delivery_id=event.delivery_id,
        event_name=event.event_name,
        repository=event.repository_full_name,
    )


def process_event(event_path: str) -> None:
    """CLI entrypoint: process a single event from a JSON file, then exit.

    Args:
        event_path: Path to the GitHub event JSON file
                    (typically ``$GITHUB_EVENT_PATH`` in Actions).
    """
    configure_logging()
    logger: structlog.stdlib.BoundLogger = structlog.get_logger("cli")

    # Validate all required environment variables upfront (fail fast).
    app_id = os.getenv("GITHUB_APP_ID", "")
    key_path = os.getenv("GITHUB_APP_PRIVATE_KEY_PATH", "")
    github_event_name = os.getenv("GITHUB_EVENT_NAME", "")

    if not app_id:
        print("GITHUB_APP_ID is not set.", file=sys.stderr)
        raise SystemExit(1)
    if not key_path:
        print("GITHUB_APP_PRIVATE_KEY_PATH is not set.", file=sys.stderr)
        raise SystemExit(1)

    github_event = _resolve_github_event(github_event_name)

    # Load private key (after all env var checks pass).
    private_key_pem = load_private_key_pem(key_path)

    # Load event payload.
    payload = _load_event_payload(event_path)

    # Generate a synthetic delivery ID for Actions mode.
    delivery_id = os.getenv("GITHUB_RUN_ID", str(uuid.uuid4()))

    event = normalize_github_event(
        github_event=github_event,
        delivery_id=delivery_id,
        payload=payload,
    )

    logger.info(
        "cli.processing_event",
        event_path=event_path,
        event_name=event.event_name,
        delivery_id=event.delivery_id,
        repository=event.repository_full_name,
    )

    # Build router.
    catalog = ModelCatalog.from_discovery()
    router = LLMRouter(model_catalog=catalog)

    # Wire hook bus.
    hook_subscriber = LoggingHookSubscriber()
    router._hook_bus.subscribe("on_llm_prompt", hook_subscriber.on_prompt)
    router._hook_bus.subscribe("on_llm_response", hook_subscriber.on_response)

    # Run.
    asyncio.run(_run_single_event(event, router, app_id, private_key_pem, logger))

    logger.info("cli.completed")
