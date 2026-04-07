"""GitHub webhook header parsing and signature verification."""

from __future__ import annotations

import hmac
from dataclasses import dataclass
from hashlib import sha256
from typing import Mapping

GITHUB_EVENT_HEADER = "x-github-event"
GITHUB_DELIVERY_HEADER = "x-github-delivery"
GITHUB_SIGNATURE_HEADER = "x-hub-signature-256"
_SIGNATURE_PREFIX = "sha256="


class WebhookVerificationError(Exception):
    """Base error for webhook parsing and signature validation."""


class MissingHeaderError(WebhookVerificationError):
    """Raised when a required GitHub webhook header is missing."""


class MalformedSignatureError(WebhookVerificationError):
    """Raised when the signature header does not match GitHub's format."""


class InvalidSignatureError(WebhookVerificationError):
    """Raised when a provided webhook signature fails verification."""


@dataclass(frozen=True, slots=True)
class GitHubWebhookHeaders:
    """Subset of webhook headers required for ingress processing."""

    github_event: str
    delivery_id: str
    signature: str


def parse_github_webhook_headers(headers: Mapping[str, str]) -> GitHubWebhookHeaders:
    """Extract and validate required GitHub webhook headers."""

    github_event = _require_header(headers, GITHUB_EVENT_HEADER)
    delivery_id = _require_header(headers, GITHUB_DELIVERY_HEADER)
    signature = _require_header(headers, GITHUB_SIGNATURE_HEADER)
    _ = parse_signature_header(signature)
    return GitHubWebhookHeaders(
        github_event=github_event,
        delivery_id=delivery_id,
        signature=signature,
    )


def parse_signature_header(signature_header: str | None) -> str:
    """Return the hex digest from an ``X-Hub-Signature-256`` header."""

    if signature_header is None or not signature_header.strip():
        raise MissingHeaderError(f"Missing required header: {GITHUB_SIGNATURE_HEADER}")
    normalized = signature_header.strip()
    if not normalized.startswith(_SIGNATURE_PREFIX):
        raise MalformedSignatureError("Signature must start with 'sha256='")
    digest = normalized[len(_SIGNATURE_PREFIX) :]
    if len(digest) != 64:
        raise MalformedSignatureError("Signature digest must be 64 hex characters")
    if any(character not in "0123456789abcdefABCDEF" for character in digest):
        raise MalformedSignatureError("Signature digest must contain only hex characters")
    return digest.lower()


def verify_webhook_signature(*, secret: str, body: bytes, signature_header: str | None) -> None:
    """Validate webhook payload bytes against the GitHub signature header."""

    provided_digest = parse_signature_header(signature_header)
    expected_digest = hmac.new(secret.encode("utf-8"), body, sha256).hexdigest()
    if not hmac.compare_digest(expected_digest, provided_digest):
        raise InvalidSignatureError("Invalid webhook signature")


def _require_header(headers: Mapping[str, str], key: str) -> str:
    direct = headers.get(key)
    if direct is not None and direct.strip():
        return direct.strip()

    for header_key, value in headers.items():
        if header_key.lower() == key and value.strip():
            return value.strip()

    raise MissingHeaderError(f"Missing required header: {key}")
