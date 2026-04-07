from __future__ import annotations

import hashlib
import hmac

import pytest

from github_auto_maintainer.server.webhooks import (
    GITHUB_DELIVERY_HEADER,
    GITHUB_EVENT_HEADER,
    GITHUB_SIGNATURE_HEADER,
    InvalidSignatureError,
    MalformedSignatureError,
    MissingHeaderError,
    parse_github_webhook_headers,
    verify_webhook_signature,
)


def _signature(secret: str, body: bytes) -> str:
    digest = hmac.new(secret.encode("utf-8"), body, hashlib.sha256).hexdigest()
    return f"sha256={digest}"


def test_verify_signature_accepts_valid_signature() -> None:
    secret = "test-secret"
    body = b'{"action":"opened"}'
    signature = _signature(secret, body)

    verify_webhook_signature(secret=secret, body=body, signature_header=signature)


def test_verify_signature_rejects_invalid_signature() -> None:
    secret = "test-secret"
    body = b'{"action":"opened"}'

    with pytest.raises(InvalidSignatureError):
        verify_webhook_signature(
            secret=secret,
            body=body,
            signature_header="sha256=" + "0" * 64,
        )


def test_verify_signature_rejects_missing_signature() -> None:
    with pytest.raises(MissingHeaderError):
        verify_webhook_signature(secret="test-secret", body=b"{}", signature_header=None)


def test_verify_signature_rejects_malformed_signature() -> None:
    with pytest.raises(MalformedSignatureError):
        verify_webhook_signature(
            secret="test-secret",
            body=b"{}",
            signature_header="sha1=abc",
        )


def test_parse_github_webhook_headers_extracts_required_values() -> None:
    headers = {
        GITHUB_EVENT_HEADER: "pull_request",
        GITHUB_DELIVERY_HEADER: "delivery-123",
        GITHUB_SIGNATURE_HEADER: "sha256=" + "a" * 64,
    }

    parsed = parse_github_webhook_headers(headers)

    assert parsed.github_event == "pull_request"
    assert parsed.delivery_id == "delivery-123"
    assert parsed.signature == "sha256=" + "a" * 64


def test_parse_github_webhook_headers_rejects_missing_event() -> None:
    with pytest.raises(MissingHeaderError):
        parse_github_webhook_headers(
            {
                GITHUB_DELIVERY_HEADER: "delivery-123",
                GITHUB_SIGNATURE_HEADER: "sha256=" + "a" * 64,
            }
        )
