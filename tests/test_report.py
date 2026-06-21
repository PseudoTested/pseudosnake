"""Tests for pseudosnake.report — JSON report assembly and output."""

import json
from pathlib import Path


from pseudosnake.discover import FunctionInfo
from pseudosnake.report import (
    build_file_entry,
    build_function_entry,
    build_mutant_entry,
    build_report,
    output_report,
)
from pseudosnake.runner import RunResult


def _make_run_result(
    exit_code: int = 1, stdout: str = "", stderr: str = ""
) -> RunResult:
    return RunResult(exit_code=exit_code, stdout=stdout, stderr=stderr, duration=0.5)


def test_build_mutant_entry_basic() -> None:
    """build_mutant_entry returns the expected keys for a mutant result."""
    result = _make_run_result(exit_code=0)
    entry = build_mutant_entry("return 0", result, "SURVIVED")
    assert entry["mutation"] == "return 0"
    assert entry["exit_code"] == 0
    assert entry["status"] == "SURVIVED"
    assert entry["pseudo_tested"] is True
    assert entry["duration"] == 0.5
    assert entry["failed_tests"] == []
    assert entry["failed_test_count"] == 0


def test_build_mutant_entry_with_run_exit_codes() -> None:
    """build_mutant_entry includes per-run exit codes when provided."""
    result = _make_run_result(exit_code=1)
    entry = build_mutant_entry(
        "return 1", result, "KILLED", run_exit_codes=[0, 1, 1], num_test_runs=3
    )
    assert entry["run_exit_codes"] == [0, 1, 1]
    assert entry["num_test_runs"] == 3


def test_build_mutant_entry_with_verification() -> None:
    """build_mutant_entry includes verification metadata when provided."""
    result = _make_run_result()
    verification = {"mutation_applied": True, "mutation_restored": True}
    entry = build_mutant_entry(
        "return 0", result, "SURVIVED", verification=verification
    )
    assert entry["verification"] == verification


def test_build_mutant_entry_with_failed_tests() -> None:
    """build_mutant_entry extracts test counts from output."""
    stdout = (
        "FAILED tests/x.py::test_a - AssertionError\n"
        "FAILED tests/y.py::test_b - ValueError\n"
        "2 failed, 3 passed in 1.0s\n"
    )
    result = _make_run_result(exit_code=1, stdout=stdout)
    entry = build_mutant_entry("return 1", result, "KILLED")
    assert entry["failed_tests"] == ["tests/x.py::test_a", "tests/y.py::test_b"]
    assert entry["failed_test_count"] == 2
    assert entry["pytest_failed_count"] == 2
    assert entry["pytest_passed_count"] == 3


def test_build_function_entry_with_class() -> None:
    """build_function_entry includes class_name when present."""
    func_info = FunctionInfo(
        name="method",
        class_name="MyClass",
        file_path=Path("mod.py"),
        line_number=10,
        body_start_line=11,
        end_line=15,
        body_col_offset=4,
        return_type="str",
    )
    entry = build_function_entry(func_info, [])
    assert entry["function_name"] == "method"
    assert entry["class_name"] == "MyClass"
    assert entry["return_type"] == "str"


def test_build_function_entry_without_class() -> None:
    """build_function_entry does not include class_name when None."""
    func_info = FunctionInfo(
        name="top_level",
        class_name=None,
        file_path=Path("mod.py"),
        line_number=1,
        body_start_line=2,
        end_line=4,
        body_col_offset=0,
        return_type="int",
    )
    entry = build_function_entry(func_info, [])
    assert "class_name" not in entry


def test_build_function_entry_uncovered() -> None:
    """build_function_entry sets covered=False when specified."""
    func_info = FunctionInfo(
        name="uncalled",
        class_name=None,
        file_path=Path("mod.py"),
        line_number=5,
        body_start_line=6,
        end_line=8,
        body_col_offset=0,
        return_type="str",
    )
    entry = build_function_entry(func_info, [], covered=False)
    assert entry["covered"] is False
    assert entry["mutants"] == []
    assert entry["function_name"] == "uncalled"


def test_build_file_entry() -> None:
    """build_file_entry returns relative path and function entries."""
    project = Path("/tmp/proj")
    file_path = project / "src" / "mod.py"
    entry = build_file_entry(file_path, project, [])
    assert entry["file"] == "src/mod.py"
    assert entry["functions"] == []


def test_build_report() -> None:
    """build_report assembles metadata and files."""
    metadata = {"start_time": "2024-01-01T00:00:00"}
    file_entries: list[dict] = []
    report = build_report(metadata, file_entries)
    assert report["metadata"] == metadata
    assert report["files"] == []


def test_output_report_to_file(tmp_path: Path) -> None:
    """output_report writes JSON to the given file."""
    report = {"key": "value"}
    output = tmp_path / "out.json"
    output_report(report, output)
    assert output.exists()
    parsed = json.loads(output.read_text())
    assert parsed == report


def test_output_report_to_stdout(capsys) -> None:
    """output_report writes to stdout when output_file is None."""
    report = {"key": "value"}
    output_report(report, None)
    captured = capsys.readouterr()
    parsed = json.loads(captured.out.strip())
    assert parsed == report


def test_build_mutant_entry_killed_status() -> None:
    """build_mutant_entry sets pseudo_tested=False when KILLED."""
    result = _make_run_result(exit_code=1)
    entry = build_mutant_entry("return 0", result, "KILLED")
    assert entry["pseudo_tested"] is False
    assert entry["status"] == "KILLED"


def test_build_mutant_entry_no_tests_status() -> None:
    """build_mutant_entry sets pseudo_tested=False for NO_TESTS."""
    result = _make_run_result(exit_code=5)
    entry = build_mutant_entry("return 0", result, "NO_TESTS")
    assert entry["pseudo_tested"] is False


def test_build_mutant_entry_crash_status() -> None:
    """build_mutant_entry sets pseudo_tested=False for CRASH."""
    result = _make_run_result(exit_code=-1)
    entry = build_mutant_entry("return 0", result, "CRASH")
    assert entry["pseudo_tested"] is False
