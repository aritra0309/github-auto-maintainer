"""Pure unified diff parser producing typed dataclasses."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Literal


@dataclass(frozen=True, slots=True)
class DiffLine:
    """Single line within a diff hunk."""

    kind: Literal["add", "remove", "context"]
    content: str
    line_number: int | None


@dataclass(frozen=True, slots=True)
class DiffHunk:
    """A contiguous hunk within a file diff."""

    old_start: int
    old_count: int
    new_start: int
    new_count: int
    header: str
    lines: tuple[DiffLine, ...]


@dataclass(frozen=True, slots=True)
class FileDiff:
    """Parsed diff for a single file."""

    old_path: str | None
    new_path: str | None
    status: Literal["added", "modified", "deleted", "renamed"]
    hunks: tuple[DiffHunk, ...]
    is_binary: bool

    @property
    def additions(self) -> int:
        return sum(
            1 for hunk in self.hunks for line in hunk.lines if line.kind == "add"
        )

    @property
    def deletions(self) -> int:
        return sum(
            1 for hunk in self.hunks for line in hunk.lines if line.kind == "remove"
        )


_DIFF_HEADER_RE = re.compile(r"^diff --git a/(.*) b/(.*)$")
_HUNK_HEADER_RE = re.compile(
    r"^@@ -(\d+)(?:,(\d+))? \+(\d+)(?:,(\d+))? @@(.*)$"
)
_BINARY_RE = re.compile(r"^Binary files .* differ$")


def parse_diff(raw: str) -> tuple[FileDiff, ...]:
    """Parse a unified diff string into typed FileDiff objects."""

    if not raw or not raw.strip():
        return ()

    lines = raw.split("\n")
    file_diffs: list[FileDiff] = []
    idx = 0

    while idx < len(lines):
        match = _DIFF_HEADER_RE.match(lines[idx])
        if not match:
            idx += 1
            continue

        a_path = match.group(1)
        b_path = match.group(2)
        idx += 1

        # Parse extended headers
        old_path: str | None = a_path
        new_path: str | None = b_path
        is_binary = False
        rename_from: str | None = None
        rename_to: str | None = None

        while idx < len(lines) and not lines[idx].startswith("diff --git "):
            line = lines[idx]

            if line.startswith("rename from "):
                rename_from = line[len("rename from "):]
                idx += 1
                continue
            if line.startswith("rename to "):
                rename_to = line[len("rename to "):]
                idx += 1
                continue
            if _BINARY_RE.match(line):
                is_binary = True
                idx += 1
                continue
            if line.startswith("--- "):
                break
            if _HUNK_HEADER_RE.match(line):
                break
            idx += 1

        # Parse --- and +++ lines
        minus_path: str | None = None
        plus_path: str | None = None

        if idx < len(lines) and lines[idx].startswith("--- "):
            minus_line = lines[idx][4:]
            if minus_line == "/dev/null":
                minus_path = None
            elif minus_line.startswith("a/"):
                minus_path = minus_line[2:]
            else:
                minus_path = minus_line
            idx += 1

        if idx < len(lines) and lines[idx].startswith("+++ "):
            plus_line = lines[idx][4:]
            if plus_line == "/dev/null":
                plus_path = None
            elif plus_line.startswith("b/"):
                plus_path = plus_line[2:]
            else:
                plus_path = plus_line
            idx += 1

        if minus_path is not None:
            old_path = minus_path
        if plus_path is not None:
            new_path = plus_path

        # Override paths for renames
        if rename_from is not None:
            old_path = rename_from
        if rename_to is not None:
            new_path = rename_to

        # Determine status
        if rename_from is not None or rename_to is not None:
            status: Literal["added", "modified", "deleted", "renamed"] = "renamed"
        elif minus_path is None and plus_path is not None:
            status = "added"
            old_path = None
        elif minus_path is not None and plus_path is None:
            status = "deleted"
            new_path = None
        elif is_binary:
            status = "modified"
        else:
            status = "modified"

        # Parse hunks
        hunks: list[DiffHunk] = []
        while idx < len(lines) and not lines[idx].startswith("diff --git "):
            hunk_match = _HUNK_HEADER_RE.match(lines[idx])
            if not hunk_match:
                idx += 1
                continue

            old_start = int(hunk_match.group(1))
            old_count = int(hunk_match.group(2)) if hunk_match.group(2) is not None else 1
            new_start = int(hunk_match.group(3))
            new_count = int(hunk_match.group(4)) if hunk_match.group(4) is not None else 1
            header = lines[idx]
            idx += 1

            hunk_lines: list[DiffLine] = []
            old_line = old_start
            new_line = new_start

            while idx < len(lines):
                hline = lines[idx]

                if hline.startswith("diff --git ") or _HUNK_HEADER_RE.match(hline):
                    break

                if hline == "\\ No newline at end of file":
                    idx += 1
                    continue

                if hline.startswith("+"):
                    hunk_lines.append(
                        DiffLine(kind="add", content=hline[1:], line_number=new_line)
                    )
                    new_line += 1
                elif hline.startswith("-"):
                    hunk_lines.append(
                        DiffLine(kind="remove", content=hline[1:], line_number=old_line)
                    )
                    old_line += 1
                elif hline.startswith(" "):
                    hunk_lines.append(
                        DiffLine(kind="context", content=hline[1:], line_number=new_line)
                    )
                    old_line += 1
                    new_line += 1
                else:
                    # Treat unexpected lines as context to handle truncated diffs
                    if hline == "":
                        # Could be trailing newline or empty context line
                        idx += 1
                        continue
                    hunk_lines.append(
                        DiffLine(kind="context", content=hline, line_number=new_line)
                    )
                    old_line += 1
                    new_line += 1

                idx += 1

            hunks.append(
                DiffHunk(
                    old_start=old_start,
                    old_count=old_count,
                    new_start=new_start,
                    new_count=new_count,
                    header=header,
                    lines=tuple(hunk_lines),
                )
            )

        file_diffs.append(
            FileDiff(
                old_path=old_path,
                new_path=new_path,
                status=status,
                hunks=tuple(hunks),
                is_binary=is_binary,
            )
        )

    return tuple(file_diffs)
