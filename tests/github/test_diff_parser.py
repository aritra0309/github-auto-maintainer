from __future__ import annotations

from pathlib import Path

from github_auto_maintainer.github.diff_parser import parse_diff

FIXTURES = Path(__file__).resolve().parent.parent / "fixtures"


def test_empty_diff_returns_empty_tuple() -> None:
    assert parse_diff("") == ()
    assert parse_diff("   \n  ") == ()


def test_small_diff_single_file() -> None:
    raw = (FIXTURES / "small_diff.patch").read_text()
    result = parse_diff(raw)
    assert len(result) == 1

    fd = result[0]
    assert fd.old_path == "src/auth.py"
    assert fd.new_path == "src/auth.py"
    assert fd.status == "modified"
    assert fd.is_binary is False
    assert fd.additions == 2
    assert fd.deletions == 0
    assert len(fd.hunks) == 1


def test_medium_diff_multiple_files() -> None:
    raw = (FIXTURES / "medium_diff.patch").read_text()
    result = parse_diff(raw)
    assert len(result) == 4

    filenames = [fd.new_path for fd in result]
    assert "src/auth.py" in filenames
    assert "src/models.py" in filenames
    assert "src/routes.py" in filenames
    assert "tests/test_auth.py" in filenames

    total_additions = sum(fd.additions for fd in result)
    total_deletions = sum(fd.deletions for fd in result)
    assert total_additions > 0
    assert total_deletions >= 0


def test_large_diff_many_changes() -> None:
    raw = (FIXTURES / "large_diff.patch").read_text()
    result = parse_diff(raw)
    assert len(result) >= 5

    total_additions = sum(fd.additions for fd in result)
    total_deletions = sum(fd.deletions for fd in result)
    total_changed = total_additions + total_deletions
    assert total_changed >= 300


def test_new_file_detection() -> None:
    raw = """diff --git a/new_file.py b/new_file.py
new file mode 100644
index 0000000..1234567
--- /dev/null
+++ b/new_file.py
@@ -0,0 +1,3 @@
+def hello():
+    return "world"
+"""
    result = parse_diff(raw)
    assert len(result) == 1
    assert result[0].status == "added"
    assert result[0].old_path is None
    assert result[0].new_path == "new_file.py"
    assert result[0].additions == 3


def test_deleted_file_detection() -> None:
    raw = """diff --git a/old_file.py b/old_file.py
deleted file mode 100644
index 1234567..0000000
--- a/old_file.py
+++ /dev/null
@@ -1,3 +0,0 @@
-def hello():
-    return "world"
-"""
    result = parse_diff(raw)
    assert len(result) == 1
    assert result[0].status == "deleted"
    assert result[0].old_path == "old_file.py"
    assert result[0].new_path is None
    assert result[0].deletions == 3


def test_rename_detection() -> None:
    raw = """diff --git a/old_name.py b/new_name.py
similarity index 100%
rename from old_name.py
rename to new_name.py
"""
    result = parse_diff(raw)
    assert len(result) == 1
    assert result[0].status == "renamed"
    assert result[0].old_path == "old_name.py"
    assert result[0].new_path == "new_name.py"


def test_binary_file_detection() -> None:
    raw = """diff --git a/image.png b/image.png
index 1234567..abcdefg 100644
Binary files a/image.png and b/image.png differ
"""
    result = parse_diff(raw)
    assert len(result) == 1
    assert result[0].is_binary is True
    assert result[0].additions == 0
    assert result[0].deletions == 0


def test_no_newline_at_end_of_file() -> None:
    raw = """diff --git a/file.txt b/file.txt
index 1234567..abcdefg 100644
--- a/file.txt
+++ b/file.txt
@@ -1,2 +1,2 @@
 hello
-world
\\ No newline at end of file
+world!
\\ No newline at end of file
"""
    result = parse_diff(raw)
    assert len(result) == 1
    assert result[0].additions == 1
    assert result[0].deletions == 1


def test_diff_line_numbers() -> None:
    raw = """diff --git a/file.py b/file.py
index 1234567..abcdefg 100644
--- a/file.py
+++ b/file.py
@@ -5,4 +5,5 @@ def foo():
     a = 1
     b = 2
+    c = 3
     return a + b
"""
    result = parse_diff(raw)
    hunk = result[0].hunks[0]

    context_lines = [line for line in hunk.lines if line.kind == "context"]
    add_lines = [line for line in hunk.lines if line.kind == "add"]

    assert context_lines[0].line_number == 5
    assert add_lines[0].line_number == 7
