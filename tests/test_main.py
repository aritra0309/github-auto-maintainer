from __future__ import annotations

import pytest

from github_auto_maintainer import __main__
from github_auto_maintainer.core.errors import RouterStartupValidationError
from github_auto_maintainer.core.llm_router import LLMRouter


def test_main_exits_with_non_zero_on_startup_validation_failure(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def fail_validation(self: LLMRouter) -> None:
        raise RouterStartupValidationError("invalid defaults")

    monkeypatch.setattr(LLMRouter, "validate_startup", fail_validation)

    with pytest.raises(SystemExit, match="1"):
        __main__.main()

    captured = capsys.readouterr()
    assert "startup validation failed" in captured.err
    assert "invalid defaults" in captured.err


def test_main_prints_validated_bootstrap_line(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
) -> None:
    def pass_validation(self: LLMRouter) -> None:
        _ = self

    monkeypatch.setenv("DEFAULT_PROVIDER", "openai")
    monkeypatch.setenv("DEFAULT_MODEL", "gpt-5.4-mini")
    monkeypatch.setattr(LLMRouter, "validate_startup", pass_validation)

    __main__.main()

    captured = capsys.readouterr()
    assert "status=validated" in captured.out
    assert "provider=openai" in captured.out
    assert "model=gpt-5.4-mini" in captured.out
