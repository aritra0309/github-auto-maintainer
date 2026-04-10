"""Async check runner for post-patch validation commands.

This runner is available for future use when the bot has a local clone.
The Phase 5 auto-fix pipeline relies on GitHub Actions CI triggered by
the opened PR. The check runner is NOT called in the main pipeline.
"""

from __future__ import annotations

import asyncio
import time
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from github_auto_maintainer.automation.safety import AllowedCommand, SafetyConfig


@dataclass(frozen=True, slots=True)
class CheckResult:
    """Result of running a single validation check."""

    command_name: str
    exit_code: int
    stdout: str
    stderr: str
    timed_out: bool
    elapsed_seconds: float


async def run_check(
    command: AllowedCommand,
    working_dir: Path,
    env: Mapping[str, str] | None = None,
) -> CheckResult:
    """Run a single allowed command and capture its output.

    Uses ``asyncio.create_subprocess_exec`` — never ``shell=True``.
    Enforces the command's timeout via ``asyncio.wait_for``.
    """
    start = time.monotonic()
    env_dict: dict[str, str] | None = dict(env) if env is not None else None

    process = await asyncio.create_subprocess_exec(
        *command.template,
        cwd=working_dir,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        env=env_dict,
    )

    try:
        stdout_bytes, stderr_bytes = await asyncio.wait_for(
            process.communicate(),
            timeout=command.timeout_seconds,
        )
        elapsed = time.monotonic() - start
        return CheckResult(
            command_name=command.name,
            exit_code=process.returncode or 0,
            stdout=stdout_bytes.decode("utf-8", errors="replace"),
            stderr=stderr_bytes.decode("utf-8", errors="replace"),
            timed_out=False,
            elapsed_seconds=round(elapsed, 3),
        )
    except asyncio.TimeoutError:
        process.kill()
        await process.wait()
        elapsed = time.monotonic() - start
        return CheckResult(
            command_name=command.name,
            exit_code=-1,
            stdout="",
            stderr="Process timed out",
            timed_out=True,
            elapsed_seconds=round(elapsed, 3),
        )


async def run_all_checks(
    config: SafetyConfig,
    working_dir: Path,
) -> tuple[CheckResult, ...]:
    """Run all allowed commands sequentially and collect results."""
    results: list[CheckResult] = []
    for command in config.allowed_commands:
        result = await run_check(command, working_dir)
        results.append(result)
    return tuple(results)
