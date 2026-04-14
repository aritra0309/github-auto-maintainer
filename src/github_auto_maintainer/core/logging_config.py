"""Structured logging configuration for production and development."""

from __future__ import annotations

import logging
import os
import sys
from collections.abc import MutableMapping
from typing import Any

import structlog

from github_auto_maintainer.core.logging_utils import redact_mapping


def _redact_processor(
    logger: Any,  # noqa: ARG001
    method_name: str,  # noqa: ARG001
    event_dict: MutableMapping[str, Any],
) -> MutableMapping[str, Any]:
    """Structlog processor that redacts sensitive values using existing utilities."""
    redacted = redact_mapping(dict(event_dict))
    event_dict.clear()
    event_dict.update(redacted)
    return event_dict


_configured = False


def configure_logging(*, log_format: str | None = None) -> None:
    """Configure structlog and stdlib logging.

    Args:
        log_format: ``"json"`` (default) for production JSON lines,
                    ``"dev"`` for coloured console output.
                    Falls back to the ``LOG_FORMAT`` env var when *None*.

    Calling this function more than once is safe — subsequent calls are no-ops.
    """
    global _configured  # noqa: PLW0603
    if _configured:
        return
    _configured = True

    raw = log_format if log_format is not None else os.getenv("LOG_FORMAT", "json")
    fmt = raw.strip().lower()

    shared_processors: list[structlog.types.Processor] = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso"),
        structlog.processors.StackInfoRenderer(),
        structlog.processors.UnicodeDecoder(),
        _redact_processor,
    ]

    if fmt == "dev":
        renderer: structlog.types.Processor = structlog.dev.ConsoleRenderer()
    else:
        renderer = structlog.processors.JSONRenderer()

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        wrapper_class=structlog.stdlib.BoundLogger,
        context_class=dict,
        logger_factory=structlog.stdlib.LoggerFactory(),
        cache_logger_on_first_use=True,
    )

    formatter = structlog.stdlib.ProcessorFormatter(
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            renderer,
        ],
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root = logging.getLogger()
    root.handlers.clear()
    root.addHandler(handler)
    root.setLevel(logging.INFO)

    # Quiet noisy third-party loggers
    for noisy in ("httpx", "httpcore", "litellm", "uvicorn.access"):
        logging.getLogger(noisy).setLevel(logging.WARNING)


def reset_logging() -> None:
    """Reset the configured flag — intended for tests only."""
    global _configured  # noqa: PLW0603
    _configured = False
    structlog.reset_defaults()
