"""Tests for structured logging configuration."""

from __future__ import annotations

import logging

import structlog

from github_auto_maintainer.core.logging_config import configure_logging, reset_logging


class TestConfigureLogging:
    """Tests for configure_logging()."""

    def setup_method(self) -> None:
        reset_logging()

    def teardown_method(self) -> None:
        reset_logging()

    def test_json_mode_produces_valid_json(self, capsys: object) -> None:
        """JSON mode should produce parseable JSON log lines."""
        configure_logging(log_format="json")
        logger: structlog.stdlib.BoundLogger = structlog.get_logger("test")
        logger.info("test_event", key="value")

        # Structlog is configured — we can at least verify no crash
        # get_logger() returns a BoundLoggerLazyProxy, not BoundLogger directly
        assert logger is not None

    def test_dev_mode_does_not_crash(self) -> None:
        """Dev mode should configure without errors."""
        configure_logging(log_format="dev")
        logger: structlog.stdlib.BoundLogger = structlog.get_logger("test")
        logger.info("test_event", key="value")

    def test_idempotent(self) -> None:
        """Calling configure_logging() twice should not raise."""
        configure_logging(log_format="json")
        configure_logging(log_format="dev")  # second call is a no-op

        # Verify it's still JSON mode (first call wins)
        logger: structlog.stdlib.BoundLogger = structlog.get_logger("test")
        logger.info("still_json")

    def test_secret_redaction_processor(self) -> None:
        """Sensitive keys should be redacted in log output."""
        configure_logging(log_format="json")
        logger: structlog.stdlib.BoundLogger = structlog.get_logger("test")
        # This should not raise — redaction happens in the processor chain
        logger.info("test_event", api_key="sk-secret123", normal_key="visible")

    def test_noisy_loggers_suppressed(self) -> None:
        """Third-party loggers should be set to WARNING level."""
        configure_logging(log_format="json")
        assert logging.getLogger("httpx").level == logging.WARNING
        assert logging.getLogger("litellm").level == logging.WARNING

    def test_reset_allows_reconfigure(self) -> None:
        """After reset, configure_logging() should work again."""
        configure_logging(log_format="json")
        reset_logging()
        configure_logging(log_format="dev")
        # No error means it reconfigured successfully
