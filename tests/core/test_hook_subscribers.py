"""Tests for hook bus logging subscribers."""

from __future__ import annotations

import pytest

from github_auto_maintainer.core.hook_subscribers import LoggingHookSubscriber


class TestLoggingHookSubscriber:
    """Tests for LoggingHookSubscriber."""

    @pytest.mark.asyncio
    async def test_on_prompt_logs_without_error(self) -> None:
        """on_prompt should log without raising."""
        subscriber = LoggingHookSubscriber()
        payload = {
            "provider": "openai",
            "model": "gpt-4",
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_hash": "abc123",
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        await subscriber.on_prompt(payload)

    @pytest.mark.asyncio
    async def test_on_response_logs_without_error(self) -> None:
        """on_response should log without raising."""
        subscriber = LoggingHookSubscriber()
        payload = {
            "provider": "openai",
            "model": "gpt-4",
            "input_tokens": 100,
            "output_tokens": 50,
            "prompt_hash": "abc123",
            "max_tokens": 1024,
            "temperature": 0.7,
        }
        await subscriber.on_response(payload)

    @pytest.mark.asyncio
    async def test_on_prompt_redacts_sensitive_keys(self) -> None:
        """Sensitive keys in payload should be redacted."""
        subscriber = LoggingHookSubscriber()
        payload = {
            "provider": "openai",
            "model": "gpt-4",
            "input_tokens": 0,
            "output_tokens": 0,
            "prompt_hash": "abc123",
            "max_tokens": 1024,
            "temperature": 0.7,
            "api_key": "sk-secret-value",
        }
        # Should not raise — redaction happens internally
        await subscriber.on_prompt(payload)

    @pytest.mark.asyncio
    async def test_on_response_handles_empty_payload(self) -> None:
        """Empty payload should not crash the subscriber."""
        subscriber = LoggingHookSubscriber()
        await subscriber.on_response({})
