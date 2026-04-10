"""Run metadata persistence for the auto-fix pipeline."""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Protocol

import aiosqlite


class RunStatus(StrEnum):
    """Status of an auto-fix run."""

    PENDING = "pending"
    PATCHING = "patching"
    CHECKING = "checking"
    PR_OPENED = "pr_opened"
    FAILED = "failed"
    REJECTED = "rejected"


@dataclass(frozen=True, slots=True)
class AutoFixRun:
    """Immutable record of a single auto-fix pipeline execution."""

    run_id: str
    delivery_id: str
    issue_number: int
    owner: str
    repo: str
    status: RunStatus
    branch_name: str | None
    pr_number: int | None
    pr_url: str | None
    model_used: str | None
    patch_files_count: int
    patch_lines_changed: int
    safety_violations: tuple[str, ...]
    error_message: str | None
    created_at: str
    completed_at: str | None


class RunStore(Protocol):
    """Protocol for run metadata stores."""

    async def create_run(self, run: AutoFixRun) -> None: ...

    async def update_run(self, run_id: str, **updates: Any) -> None: ...

    async def get_run(self, run_id: str) -> AutoFixRun | None: ...

    async def get_runs_for_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[AutoFixRun, ...]: ...

    async def get_recent_runs(self, limit: int = 50) -> tuple[AutoFixRun, ...]: ...


class InMemoryRunStore:
    """In-memory run store — state lost on restart."""

    def __init__(self) -> None:
        self._runs: dict[str, AutoFixRun] = {}

    async def create_run(self, run: AutoFixRun) -> None:
        self._runs[run.run_id] = run

    async def update_run(self, run_id: str, **updates: Any) -> None:
        existing = self._runs.get(run_id)
        if existing is None:
            return
        fields: dict[str, Any] = {
            "run_id": existing.run_id,
            "delivery_id": existing.delivery_id,
            "issue_number": existing.issue_number,
            "owner": existing.owner,
            "repo": existing.repo,
            "status": existing.status,
            "branch_name": existing.branch_name,
            "pr_number": existing.pr_number,
            "pr_url": existing.pr_url,
            "model_used": existing.model_used,
            "patch_files_count": existing.patch_files_count,
            "patch_lines_changed": existing.patch_lines_changed,
            "safety_violations": existing.safety_violations,
            "error_message": existing.error_message,
            "created_at": existing.created_at,
            "completed_at": existing.completed_at,
        }
        for key, value in updates.items():
            if key in fields:
                fields[key] = value
        self._runs[run_id] = AutoFixRun(**fields)

    async def get_run(self, run_id: str) -> AutoFixRun | None:
        return self._runs.get(run_id)

    async def get_runs_for_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[AutoFixRun, ...]:
        return tuple(
            r
            for r in self._runs.values()
            if r.owner == owner and r.repo == repo and r.issue_number == issue_number
        )

    async def get_recent_runs(self, limit: int = 50) -> tuple[AutoFixRun, ...]:
        all_runs = sorted(self._runs.values(), key=lambda r: r.created_at, reverse=True)
        return tuple(all_runs[:limit])


_CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS auto_fix_runs (
    run_id TEXT PRIMARY KEY,
    delivery_id TEXT NOT NULL,
    issue_number INTEGER NOT NULL,
    owner TEXT NOT NULL,
    repo TEXT NOT NULL,
    status TEXT NOT NULL,
    branch_name TEXT,
    pr_number INTEGER,
    pr_url TEXT,
    model_used TEXT,
    patch_files_count INTEGER NOT NULL DEFAULT 0,
    patch_lines_changed INTEGER NOT NULL DEFAULT 0,
    safety_violations TEXT NOT NULL DEFAULT '[]',
    error_message TEXT,
    created_at TEXT NOT NULL,
    completed_at TEXT
)
"""

_CREATE_ISSUE_INDEX = """
CREATE INDEX IF NOT EXISTS idx_runs_issue
ON auto_fix_runs (owner, repo, issue_number)
"""

_CREATE_STATUS_INDEX = """
CREATE INDEX IF NOT EXISTS idx_runs_status
ON auto_fix_runs (status)
"""


class SQLiteRunStore:
    """SQLite-backed run store."""

    def __init__(self, db_path: str | Path = "runs.db") -> None:
        self._db_path = str(db_path)

    async def initialize(self) -> None:
        """Create tables and indexes if they do not exist."""
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(_CREATE_TABLE)
            await db.execute(_CREATE_ISSUE_INDEX)
            await db.execute(_CREATE_STATUS_INDEX)
            await db.commit()

    async def create_run(self, run: AutoFixRun) -> None:
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(
                """
                INSERT INTO auto_fix_runs (
                    run_id, delivery_id, issue_number, owner, repo, status,
                    branch_name, pr_number, pr_url, model_used,
                    patch_files_count, patch_lines_changed, safety_violations,
                    error_message, created_at, completed_at
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    run.run_id,
                    run.delivery_id,
                    run.issue_number,
                    run.owner,
                    run.repo,
                    run.status.value,
                    run.branch_name,
                    run.pr_number,
                    run.pr_url,
                    run.model_used,
                    run.patch_files_count,
                    run.patch_lines_changed,
                    json.dumps(list(run.safety_violations)),
                    run.error_message,
                    run.created_at,
                    run.completed_at,
                ),
            )
            await db.commit()

    async def update_run(self, run_id: str, **updates: Any) -> None:
        if not updates:
            return
        set_clauses: list[str] = []
        values: list[Any] = []
        for key, value in updates.items():
            set_clauses.append(f"{key} = ?")
            if key == "safety_violations" and isinstance(value, (list, tuple)):
                values.append(json.dumps(list(value)))
            elif key == "status" and isinstance(value, RunStatus):
                values.append(value.value)
            else:
                values.append(value)
        values.append(run_id)
        sql = f"UPDATE auto_fix_runs SET {', '.join(set_clauses)} WHERE run_id = ?"
        async with aiosqlite.connect(self._db_path) as db:
            await db.execute(sql, values)
            await db.commit()

    async def get_run(self, run_id: str) -> AutoFixRun | None:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM auto_fix_runs WHERE run_id = ?", (run_id,)
            )
            row = await cursor.fetchone()
            if row is None:
                return None
            return _row_to_run(row)

    async def get_runs_for_issue(
        self, owner: str, repo: str, issue_number: int
    ) -> tuple[AutoFixRun, ...]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM auto_fix_runs WHERE owner = ? AND repo = ? AND issue_number = ? "
                "ORDER BY created_at DESC",
                (owner, repo, issue_number),
            )
            rows = await cursor.fetchall()
            return tuple(_row_to_run(row) for row in rows)

    async def get_recent_runs(self, limit: int = 50) -> tuple[AutoFixRun, ...]:
        async with aiosqlite.connect(self._db_path) as db:
            db.row_factory = aiosqlite.Row
            cursor = await db.execute(
                "SELECT * FROM auto_fix_runs ORDER BY created_at DESC LIMIT ?",
                (limit,),
            )
            rows = await cursor.fetchall()
            return tuple(_row_to_run(row) for row in rows)


def _row_to_run(row: aiosqlite.Row) -> AutoFixRun:
    """Convert a database row to an AutoFixRun."""
    violations_raw: list[str] = json.loads(str(row["safety_violations"]))
    return AutoFixRun(
        run_id=str(row["run_id"]),
        delivery_id=str(row["delivery_id"]),
        issue_number=int(row["issue_number"]),
        owner=str(row["owner"]),
        repo=str(row["repo"]),
        status=RunStatus(str(row["status"])),
        branch_name=row["branch_name"] if row["branch_name"] is not None else None,
        pr_number=int(row["pr_number"]) if row["pr_number"] is not None else None,
        pr_url=str(row["pr_url"]) if row["pr_url"] is not None else None,
        model_used=str(row["model_used"]) if row["model_used"] is not None else None,
        patch_files_count=int(row["patch_files_count"]),
        patch_lines_changed=int(row["patch_lines_changed"]),
        safety_violations=tuple(violations_raw),
        error_message=(
            str(row["error_message"]) if row["error_message"] is not None else None
        ),
        created_at=str(row["created_at"]),
        completed_at=(
            str(row["completed_at"]) if row["completed_at"] is not None else None
        ),
    )
