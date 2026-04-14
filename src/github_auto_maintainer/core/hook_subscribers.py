"""Hook bus subscribers for LLM observability logging."""

from __future__ import annotations

from typing import Any

import structlog

from github_auto_maintainer.core.logging_utils import redact_mapping


class LoggingHookSubscriber:
    """Logs LLM prompt and response events via structlog."""

    def __init__(self) -> None:
        self._logger: structlog.stdlib.BoundLogger = structlog.get_logger(
            "llm.hooks",
        )

    async def on_prompt(self, payload: dict[str, Any]) -> None:
        """Log an outgoing LLM prompt event."""
        safe = redact_mapping(payload)
        self._logger.info(
            "llm.prompt_sent",
            provider=safe.get("provider"),
            model=safe.get("model"),
            max_tokens=safe.get("max_tokens"),
            temperature=safe.get("temperature"),
            prompt_hash=safe.get("prompt_hash"),
        )

    async def on_response(self, payload: dict[str, Any]) -> None:
        """Log an incoming LLM response event."""
        safe = redact_mapping(payload)
        self._logger.info(
            "llm.response_received",
            provider=safe.get("provider"),
            model=safe.get("model"),
            input_tokens=safe.get("input_tokens"),
            output_tokens=safe.get("output_tokens"),
            prompt_hash=safe.get("prompt_hash"),
        )
