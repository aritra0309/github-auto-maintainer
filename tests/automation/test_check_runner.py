"""Tests for the async check runner."""

from __future__ import annotations

import asyncio
import sys
from pathlib import Path
from unittest.mock import AsyncMock, patch

import pytest

from github_auto_maintainer.automation.check_runner import (
    run_all_checks,
    run_check,
)
from github_auto_maintainer.automation.safety import AllowedCommand, SafetyConfig


# ── run_check ─────────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_check_echo_success(tmp_path: Path) -> None:
    """Run an allowed command that succeeds."""
    cmd = AllowedCommand(name="echo", template=("echo", "hello"), timeout_seconds=10)
    result = await run_check(cmd, tmp_path)
    assert result.command_name == "echo"
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert result.timed_out is False
    assert result.elapsed_seconds >= 0


@pytest.mark.asyncio
async def test_run_check_exit_code_nonzero(tmp_path: Path) -> None:
    """Command that exits with non-zero code."""
    cmd = AllowedCommand(
        name="false_cmd",
        template=(sys.executable, "-c", "import sys; sys.exit(1)"),
        timeout_seconds=10,
    )
    result = await run_check(cmd, tmp_path)
    assert result.exit_code == 1
    assert result.timed_out is False


@pytest.mark.asyncio
async def test_run_check_captures_stderr(tmp_path: Path) -> None:
    """Verify stderr is captured."""
    cmd = AllowedCommand(
        name="stderr_cmd",
        template=(sys.executable, "-c", "import sys; sys.stderr.write('error msg')"),
        timeout_seconds=10,
    )
    result = await run_check(cmd, tmp_path)
    assert "error msg" in result.stderr


@pytest.mark.asyncio
async def test_run_check_timeout(tmp_path: Path) -> None:
    """Command that exceeds timeout is killed."""
    cmd = AllowedCommand(
        name="slow_cmd",
        template=(sys.executable, "-c", "import time; time.sleep(30)"),
        timeout_seconds=1,
    )
    result = await run_check(cmd, tmp_path)
    assert result.timed_out is True
    assert result.exit_code == -1
    assert result.elapsed_seconds >= 0.5


@pytest.mark.asyncio
async def test_run_check_with_env(tmp_path: Path) -> None:
    """Verify custom environment variables are passed."""
    cmd = AllowedCommand(
        name="env_cmd",
        template=(sys.executable, "-c", "import os; print(os.environ.get('MY_VAR', ''))"),
        timeout_seconds=10,
    )
    result = await run_check(cmd, tmp_path, env={"MY_VAR": "test_value", "PATH": "/usr/bin"})
    assert "test_value" in result.stdout


# ── run_all_checks ────────────────────────────────────────────────


@pytest.mark.asyncio
async def test_run_all_checks_collects_results(tmp_path: Path) -> None:
    """Run multiple commands and collect all results."""
    config = SafetyConfig(
        blocked_paths=frozenset(),
        blocked_extensions=frozenset(),
        allowed_commands=(
            AllowedCommand(name="echo1", template=("echo", "one"), timeout_seconds=10),
            AllowedCommand(name="echo2", template=("echo", "two"), timeout_seconds=10),
        ),
    )
    results = await run_all_checks(config, tmp_path)
    assert len(results) == 2
    assert results[0].command_name == "echo1"
    assert results[1].command_name == "echo2"
    assert all(r.exit_code == 0 for r in results)


@pytest.mark.asyncio
async def test_run_all_checks_empty_commands(tmp_path: Path) -> None:
    """Empty allowed_commands produces empty results."""
    config = SafetyConfig(
        blocked_paths=frozenset(),
        blocked_extensions=frozenset(),
        allowed_commands=(),
    )
    results = await run_all_checks(config, tmp_path)
    assert results == ()


# ── Verify shell=True is never used ──────────────────────────────


@pytest.mark.asyncio
async def test_uses_create_subprocess_exec_not_shell(tmp_path: Path) -> None:
    """Verify that create_subprocess_exec is called, not create_subprocess_shell."""
    cmd = AllowedCommand(name="echo", template=("echo", "test"), timeout_seconds=10)

    with patch("github_auto_maintainer.automation.check_runner.asyncio") as mock_asyncio:
        # Set up the mock process
        mock_process = AsyncMock()
        mock_process.communicate = AsyncMock(return_value=(b"test\n", b""))
        mock_process.returncode = 0
        mock_process.kill = AsyncMock()
        mock_process.wait = AsyncMock()

        mock_asyncio.create_subprocess_exec = AsyncMock(return_value=mock_process)
        mock_asyncio.create_subprocess_shell = AsyncMock()
        mock_asyncio.subprocess = asyncio.subprocess
        mock_asyncio.wait_for = asyncio.wait_for
        mock_asyncio.TimeoutError = asyncio.TimeoutError

        await run_check(cmd, tmp_path)

        # create_subprocess_exec was called
        mock_asyncio.create_subprocess_exec.assert_called_once()
        # create_subprocess_shell was NOT called
        mock_asyncio.create_subprocess_shell.assert_not_called()
