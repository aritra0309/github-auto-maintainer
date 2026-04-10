"""Tests for RunStore implementations (SQLite and InMemory)."""

from __future__ import annotations

from pathlib import Path

import pytest

from github_auto_maintainer.core.run_store import (
    AutoFixRun,
    InMemoryRunStore,
    RunStatus,
    SQLiteRunStore,
)


def _make_run(
    *,
    run_id: str = "run-001",
    delivery_id: str = "delivery-001",
    issue_number: int = 42,
    owner: str = "octocat",
    repo: str = "hello-world",
    status: RunStatus = RunStatus.PENDING,
    branch_name: str | None = None,
    pr_number: int | None = None,
    pr_url: str | None = None,
    model_used: str | None = None,
    patch_files_count: int = 0,
    patch_lines_changed: int = 0,
    safety_violations: tuple[str, ...] = (),
    error_message: str | None = None,
    created_at: str = "2026-04-10T12:00:00Z",
    completed_at: str | None = None,
) -> AutoFixRun:
    return AutoFixRun(
        run_id=run_id,
        delivery_id=delivery_id,
        issue_number=issue_number,
        owner=owner,
        repo=repo,
        status=status,
        branch_name=branch_name,
        pr_number=pr_number,
        pr_url=pr_url,
        model_used=model_used,
        patch_files_count=patch_files_count,
        patch_lines_changed=patch_lines_changed,
        safety_violations=safety_violations,
        error_message=error_message,
        created_at=created_at,
        completed_at=completed_at,
    )


# ── InMemoryRunStore ──────────────────────────────────────────────


@pytest.mark.asyncio
async def test_inmemory_create_and_get() -> None:
    store = InMemoryRunStore()
    run = _make_run()
    await store.create_run(run)
    result = await store.get_run("run-001")
    assert result is not None
    assert result.run_id == "run-001"
    assert result.status == RunStatus.PENDING


@pytest.mark.asyncio
async def test_inmemory_get_nonexistent() -> None:
    store = InMemoryRunStore()
    result = await store.get_run("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_inmemory_update_status() -> None:
    store = InMemoryRunStore()
    await store.create_run(_make_run())
    await store.update_run("run-001", status=RunStatus.PR_OPENED)
    result = await store.get_run("run-001")
    assert result is not None
    assert result.status == RunStatus.PR_OPENED


@pytest.mark.asyncio
async def test_inmemory_update_completed_at() -> None:
    store = InMemoryRunStore()
    await store.create_run(_make_run())
    await store.update_run("run-001", completed_at="2026-04-10T13:00:00Z")
    result = await store.get_run("run-001")
    assert result is not None
    assert result.completed_at == "2026-04-10T13:00:00Z"


@pytest.mark.asyncio
async def test_inmemory_update_nonexistent_no_error() -> None:
    store = InMemoryRunStore()
    await store.update_run("nonexistent", status=RunStatus.FAILED)
    # Should not raise


@pytest.mark.asyncio
async def test_inmemory_get_runs_for_issue() -> None:
    store = InMemoryRunStore()
    await store.create_run(_make_run(run_id="r1", issue_number=42))
    await store.create_run(_make_run(run_id="r2", issue_number=42))
    await store.create_run(_make_run(run_id="r3", issue_number=99))

    results = await store.get_runs_for_issue("octocat", "hello-world", 42)
    assert len(results) == 2
    assert all(r.issue_number == 42 for r in results)


@pytest.mark.asyncio
async def test_inmemory_get_recent_runs() -> None:
    store = InMemoryRunStore()
    await store.create_run(_make_run(run_id="r1", created_at="2026-04-10T10:00:00Z"))
    await store.create_run(_make_run(run_id="r2", created_at="2026-04-10T12:00:00Z"))
    await store.create_run(_make_run(run_id="r3", created_at="2026-04-10T11:00:00Z"))

    results = await store.get_recent_runs(limit=2)
    assert len(results) == 2
    # Most recent first
    assert results[0].run_id == "r2"
    assert results[1].run_id == "r3"


# ── SQLiteRunStore ────────────────────────────────────────────────


@pytest.fixture
def sqlite_db_path(tmp_path: Path) -> Path:
    return tmp_path / "test_runs.db"


@pytest.mark.asyncio
async def test_sqlite_initialize_and_create(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    run = _make_run()
    await store.create_run(run)
    result = await store.get_run("run-001")
    assert result is not None
    assert result.run_id == "run-001"
    assert result.status == RunStatus.PENDING


@pytest.mark.asyncio
async def test_sqlite_get_nonexistent(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    result = await store.get_run("nonexistent")
    assert result is None


@pytest.mark.asyncio
async def test_sqlite_update_status(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    await store.create_run(_make_run())
    await store.update_run("run-001", status=RunStatus.PR_OPENED)
    result = await store.get_run("run-001")
    assert result is not None
    assert result.status == RunStatus.PR_OPENED


@pytest.mark.asyncio
async def test_sqlite_update_completed_at(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    await store.create_run(_make_run())
    await store.update_run("run-001", completed_at="2026-04-10T13:00:00Z")
    result = await store.get_run("run-001")
    assert result is not None
    assert result.completed_at == "2026-04-10T13:00:00Z"


@pytest.mark.asyncio
async def test_sqlite_update_safety_violations(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    await store.create_run(_make_run())
    await store.update_run(
        "run-001",
        safety_violations=("blocked_path: .env", "diff_too_large: 600 lines"),
    )
    result = await store.get_run("run-001")
    assert result is not None
    assert len(result.safety_violations) == 2
    assert "blocked_path: .env" in result.safety_violations


@pytest.mark.asyncio
async def test_sqlite_get_runs_for_issue(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    await store.create_run(_make_run(run_id="r1", issue_number=42))
    await store.create_run(_make_run(run_id="r2", issue_number=42))
    await store.create_run(_make_run(run_id="r3", issue_number=99))

    results = await store.get_runs_for_issue("octocat", "hello-world", 42)
    assert len(results) == 2
    assert all(r.issue_number == 42 for r in results)


@pytest.mark.asyncio
async def test_sqlite_get_recent_runs_ordering(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    await store.create_run(_make_run(run_id="r1", created_at="2026-04-10T10:00:00Z"))
    await store.create_run(_make_run(run_id="r2", created_at="2026-04-10T12:00:00Z"))
    await store.create_run(_make_run(run_id="r3", created_at="2026-04-10T11:00:00Z"))

    results = await store.get_recent_runs(limit=50)
    assert len(results) == 3
    assert results[0].run_id == "r2"
    assert results[1].run_id == "r3"
    assert results[2].run_id == "r1"


@pytest.mark.asyncio
async def test_sqlite_get_recent_runs_limit(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    for i in range(5):
        await store.create_run(
            _make_run(run_id=f"r{i}", created_at=f"2026-04-10T{10+i:02d}:00:00Z")
        )

    results = await store.get_recent_runs(limit=2)
    assert len(results) == 2


@pytest.mark.asyncio
async def test_sqlite_preserves_nullable_fields(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    run = _make_run(
        branch_name=None,
        pr_number=None,
        pr_url=None,
        model_used=None,
        error_message=None,
        completed_at=None,
    )
    await store.create_run(run)
    result = await store.get_run("run-001")
    assert result is not None
    assert result.branch_name is None
    assert result.pr_number is None
    assert result.pr_url is None
    assert result.model_used is None
    assert result.error_message is None
    assert result.completed_at is None


@pytest.mark.asyncio
async def test_sqlite_preserves_full_fields(sqlite_db_path: Path) -> None:
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    run = _make_run(
        branch_name="auto-fix/issue-42",
        pr_number=99,
        pr_url="https://github.com/o/r/pull/99",
        model_used="gpt-4",
        patch_files_count=3,
        patch_lines_changed=50,
        safety_violations=("violation1",),
        error_message="something went wrong",
        completed_at="2026-04-10T14:00:00Z",
    )
    await store.create_run(run)
    result = await store.get_run("run-001")
    assert result is not None
    assert result.branch_name == "auto-fix/issue-42"
    assert result.pr_number == 99
    assert result.pr_url == "https://github.com/o/r/pull/99"
    assert result.model_used == "gpt-4"
    assert result.patch_files_count == 3
    assert result.patch_lines_changed == 50
    assert result.safety_violations == ("violation1",)
    assert result.error_message == "something went wrong"
    assert result.completed_at == "2026-04-10T14:00:00Z"


@pytest.mark.asyncio
async def test_sqlite_double_initialize_idempotent(sqlite_db_path: Path) -> None:
    """Calling initialize() twice should not fail."""
    store = SQLiteRunStore(db_path=sqlite_db_path)
    await store.initialize()
    await store.initialize()
    await store.create_run(_make_run())
    result = await store.get_run("run-001")
    assert result is not None
