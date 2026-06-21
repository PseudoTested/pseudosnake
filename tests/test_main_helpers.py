"""Tests for pure helper functions from pseudosnake.helpers."""

from datetime import datetime, timezone
from pathlib import Path

from pseudosnake.discover import FunctionInfo
from pseudosnake.helpers import build_metadata, build_uncovered_entry, digest


def test_digest_returns_consistent_hash() -> None:
    """digest returns the same hash for the same content."""
    h1 = digest("hello")
    h2 = digest("hello")
    h3 = digest("world")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_digest_empty_string() -> None:
    """digest handles empty string."""
    result = digest("")
    assert len(result) == 64


def test_build_metadata_basic() -> None:
    """build_metadata returns a dict with all expected keys."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
    meta = build_metadata(
        start_time=start,
        end_time=end,
        project_dir=Path("/proj"),
        source_dir=Path("/proj/src"),
        file_arg=None,
        output_file=Path("/proj/output.json"),
        test_command="pytest tests/",
        num_test_runs=3,
        dynamic_coverage_enabled=True,
        dynamically_executed_functions=5,
        files_detected=10,
    )
    assert meta["start_time"] == "2024-01-01T00:00:00+00:00"
    assert meta["end_time"] == "2024-01-01T01:00:00+00:00"
    assert meta["project_directory"] == str(Path("/proj"))
    assert meta["source_directory"] == str(Path("/proj/src"))
    assert meta["file_argument"] is None
    assert meta["output_file"] == str(Path("/proj/output.json"))
    assert meta["test_command"] == "pytest tests/"
    assert meta["num_test_runs"] == 3
    assert meta["dynamic_coverage_enabled"] is True
    assert meta["dynamically_executed_functions"] == 5
    assert meta["files_detected"] == 10
    assert "python_version" in meta
    assert "operating_system" in meta
    assert "backup_directory" in meta


def test_build_metadata_none_source_dir_and_file() -> None:
    """build_metadata sets source_directory and file_argument to None."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    meta = build_metadata(
        start_time=start,
        end_time=end,
        project_dir=Path("/proj"),
        source_dir=None,
        file_arg=None,
        output_file=None,
        test_command="pytest",
        num_test_runs=1,
        dynamic_coverage_enabled=False,
        dynamically_executed_functions=0,
        files_detected=0,
    )
    assert meta["source_directory"] is None
    assert meta["file_argument"] is None
    assert meta["output_file"] is None


def test_build_metadata_with_file_arg() -> None:
    """build_metadata records file_argument when provided."""
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 1, tzinfo=timezone.utc)
    meta = build_metadata(
        start_time=start,
        end_time=end,
        project_dir=Path("/proj"),
        source_dir=None,
        file_arg=Path("/proj/src/mod.py"),
        output_file=None,
        test_command="pytest",
        num_test_runs=1,
        dynamic_coverage_enabled=False,
        dynamically_executed_functions=0,
        files_detected=1,
    )
    assert meta["file_argument"] == str(Path("/proj/src/mod.py"))


def test_build_uncovered_entry_top_level() -> None:
    """build_uncovered_entry returns covered=False with empty mutants."""
    func = FunctionInfo(
        name="my_func",
        class_name=None,
        file_path=Path("/x.py"),
        line_number=1,
        body_start_line=2,
        end_line=5,
        body_col_offset=0,
        return_type="int",
    )
    entry = build_uncovered_entry(func)
    assert entry["function_name"] == "my_func"
    assert entry["covered"] is False
    assert entry["mutants"] == []
    assert entry["return_type"] == "int"
    assert "class_name" not in entry


def test_build_uncovered_entry_with_class() -> None:
    """build_uncovered_entry includes class_name when present."""
    func = FunctionInfo(
        name="method",
        class_name="MyClass",
        file_path=Path("/x.py"),
        line_number=10,
        body_start_line=11,
        end_line=15,
        body_col_offset=4,
        return_type="str",
    )
    entry = build_uncovered_entry(func)
    assert entry["class_name"] == "MyClass"
    assert entry["covered"] is False
