"""Tests for the safety guardrails module."""

from __future__ import annotations

import pytest

from github_auto_maintainer.automation.safety import (
    AllowedCommand,
    SafetyConfig,
    SafetyViolation,
    default_safety_config,
    is_command_allowed,
    resolve_command,
    validate_diff_size,
    validate_patch_paths,
    validate_single_file_size,
)
from github_auto_maintainer.core.errors import SafetyError


# ── Fixtures ──────────────────────────────────────────────────────────


@pytest.fixture
def config() -> SafetyConfig:
    return default_safety_config()


# ── default_safety_config ─────────────────────────────────────────────


def test_default_config_has_blocked_paths() -> None:
    cfg = default_safety_config()
    assert ".env" in cfg.blocked_paths
    assert ".github/workflows/" in cfg.blocked_paths
    assert "secrets/" in cfg.blocked_paths


def test_default_config_has_blocked_extensions() -> None:
    cfg = default_safety_config()
    assert ".pem" in cfg.blocked_extensions
    assert ".key" in cfg.blocked_extensions
    assert ".p12" in cfg.blocked_extensions


def test_default_config_has_allowed_commands() -> None:
    cfg = default_safety_config()
    names = {cmd.name for cmd in cfg.allowed_commands}
    assert names == {"ruff", "mypy", "pytest"}


# ── validate_patch_paths: blocked exact match ─────────────────────────


def test_blocked_path_exact_match(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, [".env"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_path"
    assert violations[0].path == ".env"


# ── validate_patch_paths: .env.example NOT blocked (policy decision) ──


def test_env_example_not_blocked(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, [".env.example"])
    assert len(violations) == 0


# ── validate_patch_paths: prefix/directory match ──────────────────────


def test_blocked_path_directory_prefix(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["secrets/nested/file.py"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_path"


def test_blocked_path_github_workflows(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, [".github/workflows/ci.yml"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_path"


def test_blocked_path_github_actions(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, [".github/actions/custom/action.yml"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_path"


# ── validate_patch_paths: glob match ─────────────────────────────────


def test_blocked_path_glob_pem(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["foo.pem"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_path"


def test_blocked_path_glob_key(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["certs/server.key"])
    assert len(violations) == 1
    # Could be blocked_path (glob) or blocked_extension — both are valid
    assert violations[0].rule in {"blocked_path", "blocked_extension"}


# ── validate_patch_paths: blocked extension ──────────────────────────


def test_blocked_extension_p12(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["cert.p12"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_extension"


def test_blocked_extension_pfx(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["keystore.pfx"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_extension"


def test_blocked_extension_jks(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["keystore.jks"])
    assert len(violations) == 1
    assert violations[0].rule == "blocked_extension"


# ── validate_patch_paths: path traversal ─────────────────────────────


def test_path_traversal_rejected(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["../../etc/passwd"])
    assert len(violations) == 1
    assert violations[0].rule == "path_traversal"
    assert violations[0].path == "../../etc/passwd"


def test_path_traversal_nested(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["src/../../../etc/shadow"])
    assert len(violations) == 1
    assert violations[0].rule == "path_traversal"


# ── validate_patch_paths: safe paths ─────────────────────────────────


def test_safe_path_no_violations(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, ["src/app/main.py", "tests/test_app.py"])
    assert len(violations) == 0


def test_empty_file_list_no_violations(config: SafetyConfig) -> None:
    violations = validate_patch_paths(config, [])
    assert len(violations) == 0


# ── validate_diff_size ───────────────────────────────────────────────


def test_diff_size_over_limit(config: SafetyConfig) -> None:
    violations = validate_diff_size(config, diff_lines=501, files_changed=1)
    assert len(violations) == 1
    assert violations[0].rule == "diff_too_large"


def test_diff_size_under_limit(config: SafetyConfig) -> None:
    violations = validate_diff_size(config, diff_lines=100, files_changed=5)
    assert len(violations) == 0


def test_diff_size_at_limit(config: SafetyConfig) -> None:
    violations = validate_diff_size(config, diff_lines=500, files_changed=10)
    assert len(violations) == 0


def test_files_changed_over_limit(config: SafetyConfig) -> None:
    violations = validate_diff_size(config, diff_lines=10, files_changed=11)
    assert len(violations) == 1
    assert violations[0].rule == "too_many_files"


def test_both_limits_exceeded(config: SafetyConfig) -> None:
    violations = validate_diff_size(config, diff_lines=999, files_changed=99)
    assert len(violations) == 2
    rules = {v.rule for v in violations}
    assert rules == {"diff_too_large", "too_many_files"}


# ── validate_single_file_size ────────────────────────────────────────


def test_single_file_over_limit(config: SafetyConfig) -> None:
    violations = validate_single_file_size(config, "big.py", 201)
    assert len(violations) == 1
    assert violations[0].rule == "single_file_too_large"


def test_single_file_under_limit(config: SafetyConfig) -> None:
    violations = validate_single_file_size(config, "small.py", 50)
    assert len(violations) == 0


def test_single_file_at_limit(config: SafetyConfig) -> None:
    violations = validate_single_file_size(config, "exact.py", 200)
    assert len(violations) == 0


# ── resolve_command ──────────────────────────────────────────────────


def test_resolve_command_ruff(config: SafetyConfig) -> None:
    cmd = resolve_command(config, "ruff")
    assert cmd.name == "ruff"
    assert cmd.template == ("ruff", "check", "--fix", ".")


def test_resolve_command_mypy(config: SafetyConfig) -> None:
    cmd = resolve_command(config, "mypy")
    assert cmd.name == "mypy"


def test_resolve_command_pytest(config: SafetyConfig) -> None:
    cmd = resolve_command(config, "pytest")
    assert cmd.name == "pytest"
    assert cmd.timeout_seconds == 300


def test_resolve_command_unknown_raises(config: SafetyConfig) -> None:
    with pytest.raises(SafetyError, match="rm"):
        resolve_command(config, "rm")


# ── is_command_allowed ───────────────────────────────────────────────


def test_is_command_allowed_ruff(config: SafetyConfig) -> None:
    assert is_command_allowed(config, "ruff") is True


def test_is_command_allowed_rm(config: SafetyConfig) -> None:
    assert is_command_allowed(config, "rm") is False


def test_is_command_allowed_empty_name(config: SafetyConfig) -> None:
    assert is_command_allowed(config, "") is False


# ── Custom SafetyConfig ─────────────────────────────────────────────


def test_custom_config_overrides_defaults() -> None:
    custom = SafetyConfig(
        blocked_paths=frozenset({"custom/"}),
        blocked_extensions=frozenset({".xyz"}),
        max_diff_lines=10,
        max_files_changed=2,
        max_single_file_lines=5,
        allowed_commands=(
            AllowedCommand(name="echo", template=("echo", "hi"), timeout_seconds=5),
        ),
    )
    assert validate_patch_paths(custom, ["custom/file.py"])[0].rule == "blocked_path"
    assert validate_patch_paths(custom, [".env"]) == ()
    assert validate_diff_size(custom, 11, 1)[0].rule == "diff_too_large"
    assert is_command_allowed(custom, "echo") is True
    assert is_command_allowed(custom, "ruff") is False


# ── SafetyViolation construction ─────────────────────────────────────


def test_safety_violation_is_frozen() -> None:
    v = SafetyViolation(rule="test", detail="detail", path="file.py")
    with pytest.raises(AttributeError):
        v.rule = "changed"  # type: ignore[misc]
