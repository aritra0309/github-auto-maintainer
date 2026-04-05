from __future__ import annotations

from github_auto_maintainer.core.logging_utils import REDACTED, REQUIRED_LOG_FIELDS, build_log_record, redact_mapping


def test_build_log_record_contains_required_fields() -> None:
    record = build_log_record(
        message="processing request",
        request_id="req-1",
        event_type="pull_request.opened",
        delivery_id="delivery-1",
        provider="openai",
        model="gpt-5.4-mini",
        latency_ms=12.3,
        error_class=None,
    )

    for field in REQUIRED_LOG_FIELDS:
        assert field in record
    assert record["message"] == "processing request"
    assert record["latency_ms"] == 12.3


def test_redact_mapping_masks_sensitive_keys_and_text_patterns() -> None:
    private_key = "-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----"
    payload = {
        "api_key": "sk-abc123secret",
        "authorization": "Bearer mytokenvalue",
        "nested": {
            "token": "ghp_secret_token",
            "message": f"credentials {private_key}",
        },
        "items": ["github_pat_aaaaaaaaaaaaaaaaaaaa", "safe"],
    }

    redacted = redact_mapping(payload)

    assert redacted["api_key"] == REDACTED
    assert redacted["authorization"] == REDACTED
    nested = redacted["nested"]
    assert isinstance(nested, dict)
    assert nested["token"] == REDACTED
    message = nested["message"]
    assert isinstance(message, str)
    assert REDACTED in message
    items = redacted["items"]
    assert isinstance(items, list)
    assert items[0] == REDACTED
    assert items[1] == "safe"


def test_build_log_record_redacts_message_and_extra() -> None:
    record = build_log_record(
        message="Authorization: Bearer super-secret",
        request_id="req-2",
        event_type="issues.opened",
        delivery_id="delivery-2",
        provider="openai",
        model="gpt-5.4-mini",
        latency_ms=1.0,
        error_class="SomeError",
        extra={"private_key": "-----BEGIN PRIVATE KEY-----x-----END PRIVATE KEY-----"},
    )

    message = record["message"]
    assert isinstance(message, str)
    assert "super-secret" not in message
    extra = record["extra"]
    assert isinstance(extra, dict)
    assert extra["private_key"] == REDACTED
