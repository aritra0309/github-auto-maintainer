"""Logging helpers with explicit contract fields and secret redaction."""

from __future__ import annotations

import re
from collections.abc import Mapping, Sequence

REDACTED = "[REDACTED]"

REQUIRED_LOG_FIELDS: tuple[str, ...] = (
    "request_id",
    "event_type",
    "delivery_id",
    "provider",
    "model",
    "latency_ms",
    "error_class",
)

_SENSITIVE_KEY_PATTERN = re.compile(
    r"token|api[_-]?key|authorization|bearer|private[_-]?key|secret|password",
    flags=re.IGNORECASE,
)
_BEARER_PATTERN = re.compile(r"(?i)\bbearer\s+[a-z0-9._~+\-/]+=*")
_PRIVATE_KEY_PATTERN = re.compile(
    r"-----BEGIN [A-Z ]*PRIVATE KEY-----[\s\S]*?-----END [A-Z ]*PRIVATE KEY-----"
)
_TOKEN_PATTERN = re.compile(r"(?i)(gh[pousr]_[a-z0-9]{10,}|github_pat_[a-z0-9_]{20,}|sk-[a-z0-9]{10,})")


def build_log_record(
    *,
    message: str,
    request_id: str | None,
    event_type: str | None,
    delivery_id: str | None,
    provider: str | None,
    model: str | None,
    latency_ms: float | None,
    error_class: str | None,
    extra: Mapping[str, object] | None = None,
) -> dict[str, object]:
    """Build a structured log record with required fields and redacted values."""

    record: dict[str, object] = {
        "message": redact_text(message),
        "request_id": _redact_nullable_text(request_id),
        "event_type": _redact_nullable_text(event_type),
        "delivery_id": _redact_nullable_text(delivery_id),
        "provider": _redact_nullable_text(provider),
        "model": _redact_nullable_text(model),
        "latency_ms": latency_ms,
        "error_class": _redact_nullable_text(error_class),
    }
    if extra is not None:
        record["extra"] = redact_mapping(extra)
    return record


def redact_mapping(values: Mapping[str, object]) -> dict[str, object]:
    """Return a recursively redacted copy of a mapping for safe logging."""

    redacted: dict[str, object] = {}
    for key, value in values.items():
        if _SENSITIVE_KEY_PATTERN.search(key):
            redacted[key] = REDACTED
            continue
        redacted[key] = _redact_value(value)
    return redacted


def redact_text(text: str) -> str:
    """Redact tokens and private keys in free-form text."""

    redacted = _PRIVATE_KEY_PATTERN.sub(REDACTED, text)
    redacted = _BEARER_PATTERN.sub("Bearer [REDACTED]", redacted)
    redacted = _TOKEN_PATTERN.sub(REDACTED, redacted)
    return redacted


def _redact_nullable_text(value: str | None) -> str | None:
    if value is None:
        return None
    return redact_text(value)


def _redact_value(value: object) -> object:
    if isinstance(value, str):
        return redact_text(value)
    if isinstance(value, Mapping):
        mapped: dict[str, object] = {}
        for key, child_value in value.items():
            mapped[str(key)] = child_value
        return redact_mapping(mapped)
    if isinstance(value, Sequence) and not isinstance(value, (str, bytes, bytearray)):
        return [_redact_value(item) for item in value]
    return value
