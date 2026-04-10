"""Safety guardrails for the auto-fix pipeline.

Policy decision: ``.env.example`` is explicitly NOT blocked even though ``.env``
is on the blocked-paths list.  The ``validate_patch_paths`` function performs an
exact-match check for ``.env`` which does not match ``.env.example``.  This is
intentional — ``.env.example`` is a documentation file that should be editable by
the bot, while ``.env`` contains secrets and must never be touched.
"""

from __future__ import annotations

import fnmatch
import os
from collections.abc import Sequence
from dataclasses import dataclass

from github_auto_maintainer.core.errors import SafetyError


@dataclass(frozen=True, slots=True)
class AllowedCommand:
    """An explicitly whitelisted command template."""

    name: str
    template: tuple[str, ...]
    timeout_seconds: int = 60


@dataclass(frozen=True, slots=True)
class SafetyConfig:
    """Safety limits and allowlists for the auto-fix pipeline."""

    blocked_paths: frozenset[str]
    blocked_extensions: frozenset[str]
    max_diff_lines: int = 500
    max_files_changed: int = 10
    max_single_file_lines: int = 200
    allowed_commands: tuple[AllowedCommand, ...] = ()


@dataclass(frozen=True, slots=True)
class SafetyViolation:
    """A single safety rule violation."""

    rule: str
    detail: str
    path: str | None


def default_safety_config() -> SafetyConfig:
    """Return production-default safety configuration."""
    return SafetyConfig(
        blocked_paths=frozenset({
            ".github/workflows/",
            ".github/actions/",
            ".env",
            "*.pem",
            "*.key",
            "secrets/",
            ".secrets/",
        }),
        blocked_extensions=frozenset({".pem", ".key", ".p12", ".pfx", ".jks"}),
        max_diff_lines=500,
        max_files_changed=10,
        max_single_file_lines=200,
        allowed_commands=(
            AllowedCommand(
                name="ruff",
                template=("ruff", "check", "--fix", "."),
                timeout_seconds=60,
            ),
            AllowedCommand(
                name="mypy",
                template=("mypy", "."),
                timeout_seconds=120,
            ),
            AllowedCommand(
                name="pytest",
                template=("pytest", "-x", "-q"),
                timeout_seconds=300,
            ),
        ),
    )


def validate_patch_paths(
    config: SafetyConfig, file_paths: Sequence[str]
) -> tuple[SafetyViolation, ...]:
    """Check each path against blocked paths, blocked extensions, and path traversal."""
    violations: list[SafetyViolation] = []
    for path in file_paths:
        # Path traversal check
        if ".." in path.split("/"):
            violations.append(
                SafetyViolation(
                    rule="path_traversal",
                    detail=f"Path contains '..': {path}",
                    path=path,
                )
            )
            continue

        # Blocked paths check
        for blocked in config.blocked_paths:
            if blocked.endswith("/"):
                # Prefix/directory match
                if path.startswith(blocked) or path == blocked.rstrip("/"):
                    violations.append(
                        SafetyViolation(
                            rule="blocked_path",
                            detail=f"Path matches blocked directory '{blocked}': {path}",
                            path=path,
                        )
                    )
                    break
            elif "*" in blocked:
                # Glob match — match against the full path and the basename
                if fnmatch.fnmatch(path, blocked) or fnmatch.fnmatch(
                    os.path.basename(path), blocked
                ):
                    violations.append(
                        SafetyViolation(
                            rule="blocked_path",
                            detail=f"Path matches blocked pattern '{blocked}': {path}",
                            path=path,
                        )
                    )
                    break
            else:
                # Exact match
                if path == blocked:
                    violations.append(
                        SafetyViolation(
                            rule="blocked_path",
                            detail=f"Path matches blocked path '{blocked}': {path}",
                            path=path,
                        )
                    )
                    break
        else:
            # No blocked path matched — check extensions
            _, ext = os.path.splitext(path)
            if ext and ext in config.blocked_extensions:
                violations.append(
                    SafetyViolation(
                        rule="blocked_extension",
                        detail=f"File extension '{ext}' is blocked: {path}",
                        path=path,
                    )
                )

    return tuple(violations)


def validate_diff_size(
    config: SafetyConfig, diff_lines: int, files_changed: int
) -> tuple[SafetyViolation, ...]:
    """Check aggregate diff size and file count limits."""
    violations: list[SafetyViolation] = []
    if diff_lines > config.max_diff_lines:
        violations.append(
            SafetyViolation(
                rule="diff_too_large",
                detail=(
                    f"Diff has {diff_lines} lines, "
                    f"limit is {config.max_diff_lines}"
                ),
                path=None,
            )
        )
    if files_changed > config.max_files_changed:
        violations.append(
            SafetyViolation(
                rule="too_many_files",
                detail=(
                    f"Patch changes {files_changed} files, "
                    f"limit is {config.max_files_changed}"
                ),
                path=None,
            )
        )
    return tuple(violations)


def validate_single_file_size(
    config: SafetyConfig, path: str, lines: int
) -> tuple[SafetyViolation, ...]:
    """Check per-file line count limit."""
    if lines > config.max_single_file_lines:
        return (
            SafetyViolation(
                rule="single_file_too_large",
                detail=(
                    f"File '{path}' has {lines} lines, "
                    f"limit is {config.max_single_file_lines}"
                ),
                path=path,
            ),
        )
    return ()


def resolve_command(config: SafetyConfig, command_name: str) -> AllowedCommand:
    """Return the matching AllowedCommand or raise SafetyError."""
    for cmd in config.allowed_commands:
        if cmd.name == command_name:
            return cmd
    raise SafetyError(
        f"Command '{command_name}' is not in the allowed commands list"
    )


def is_command_allowed(config: SafetyConfig, command_name: str) -> bool:
    """Check if a command is in the allowlist (no raise)."""
    return any(cmd.name == command_name for cmd in config.allowed_commands)
