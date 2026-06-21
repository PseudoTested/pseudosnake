"""Tests for pseudosnake — CLI, mutation testing pipeline, helper functions, and entry point."""

from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from pseudosnake.discover import FunctionInfo
from pseudosnake.helpers import build_metadata, build_uncovered_entry, digest
from pseudosnake.main import (
    _print_mutant_result,
    _process_file,
    _run_dynamic_coverage_phase,
    _validate_baseline,
)
from pseudosnake.runner import AggregateRunResult, RunResult


@pytest.fixture(autouse=True)
def _silence_console(monkeypatch):
    """Mock the Rich console to suppress output in all tests."""
    mock_console = MagicMock()
    monkeypatch.setattr("pseudosnake.main.console", mock_console)
    return mock_console


def test_validate_baseline_passes_when_all_runs_succeed(tmp_path: Path) -> None:
    """_validate_baseline returns normally when baseline passes."""
    with patch("pseudosnake.main.run_tests_repeated") as mock_run:
        mock_run.return_value = AggregateRunResult(
            overall=RunResult(exit_code=0, stdout="", stderr="", duration=0.1),
            per_run=[RunResult(exit_code=0, stdout="", stderr="", duration=0.1)],
        )
        _validate_baseline("pytest", tmp_path, 3)
        mock_run.assert_called_once()


def test_validate_baseline_exits_on_failure(tmp_path: Path) -> None:
    """_validate_baseline raises SystemExit when baseline fails."""
    import typer

    with patch("pseudosnake.main.run_tests_repeated") as mock_run:
        mock_run.return_value = AggregateRunResult(
            overall=RunResult(exit_code=1, stdout="FAILED", stderr="", duration=0.1),
            per_run=[RunResult(exit_code=1, stdout="FAILED", stderr="", duration=0.1)],
        )
        try:
            _validate_baseline("pytest", tmp_path, 3)
            assert False, "should have raised"
        except typer.Exit as e:
            assert e.exit_code == 2


def test_validate_baseline_exits_on_pytest_no_tests(tmp_path: Path) -> None:
    """_validate_baseline exits when pytest finds no tests."""
    import typer

    with patch("pseudosnake.main.run_tests_repeated") as mock_run:
        mock_run.return_value = AggregateRunResult(
            overall=RunResult(exit_code=5, stdout="", stderr="", duration=0.1),
            per_run=[RunResult(exit_code=5, stdout="", stderr="", duration=0.1)],
        )
        try:
            _validate_baseline("pytest", tmp_path, 3)
            assert False, "should have raised"
        except typer.Exit as e:
            assert e.exit_code == 2


def test_run_dynamic_coverage_phase_instruments_files(tmp_path: Path) -> None:
    """_run_dynamic_coverage_phase instruments source and test files."""
    source_file = tmp_path / "source.py"
    source_file.write_text("def foo(): return 1")

    with (
        patch("pseudosnake.main.find_test_files") as mock_find_tests,
        patch("pseudosnake.main.find_functions") as mock_find_funcs,
        patch("pseudosnake.main.backup_file") as mock_backup,
        patch("pseudosnake.main.restore_file"),
        patch("pseudosnake.main.cleanup_backup"),
        patch("pseudosnake.main.run_tests_with_env"),
        patch("pseudosnake.main.load_dynamic_coverage_results") as mock_load,
        patch("pseudosnake.main.collect_executed_function_keys") as mock_collect,
    ):
        mock_find_tests.return_value = []
        mock_find_funcs.return_value = []
        mock_backup.return_value = tmp_path / "backup"
        mock_load.return_value = {}
        mock_collect.return_value = []

        result = _run_dynamic_coverage_phase([source_file], tmp_path, "pytest")
        assert result == {}


def test_process_file_returns_none_when_no_functions(tmp_path: Path) -> None:
    """_process_file returns None when file has no functions."""
    test_file = tmp_path / "empty.py"
    test_file.write_text("x = 1")

    with patch("pseudosnake.main.find_functions") as mock_find:
        mock_find.return_value = []
        result = _process_file(test_file, tmp_path, "pytest", 1, MagicMock())
        assert result is None


def test_print_mutant_result_killed_is_green(_silence_console) -> None:
    """_print_mutant_result uses green for KILLED status."""
    _print_mutant_result("test_func", "return None", "KILLED")
    _silence_console.print.assert_called_once()
    assert "green" in _silence_console.print.call_args[0][0]


def test_print_mutant_result_survived_is_red(_silence_console) -> None:
    """_print_mutant_result uses red for SURVIVED status."""
    _print_mutant_result("test_func", "return None", "SURVIVED")
    assert "red" in _silence_console.print.call_args[0][0]


def test_print_mutant_result_no_tests_is_yellow(_silence_console) -> None:
    """_print_mutant_result uses yellow for NO_TESTS status."""
    _print_mutant_result("test_func", "return None", "NO_TESTS")
    assert "yellow" in _silence_console.print.call_args[0][0]


def test_print_mutant_result_crash_is_yellow(_silence_console) -> None:
    """_print_mutant_result uses yellow for CRASH status."""
    _print_mutant_result("test_func", "return None", "CRASH")
    assert "yellow" in _silence_console.print.call_args[0][0]


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


def test_main_module_importable() -> None:
    """__main__ module is importable."""
    import pseudosnake.__main__  # noqa: F401


# --- tests for uncovered pipeline internals ---


def _ok_agg():
    return AggregateRunResult(
        overall=RunResult(exit_code=0, stdout="ok", stderr="", duration=0.1),
        per_run=[RunResult(exit_code=0, stdout="ok", stderr="", duration=0.1)],
    )


def _fail_agg(code=1):
    return AggregateRunResult(
        overall=RunResult(exit_code=code, stdout="fail", stderr="", duration=0.1),
        per_run=[RunResult(exit_code=code, stdout="fail", stderr="", duration=0.1)],
    )


def test_dynamic_coverage_with_real_files(tmp_path, monkeypatch):
    """_run_dynamic_coverage_phase processes real source and test files."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> int:\n    return 1\n")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_mod.py"
    test_file.write_text("def test_f():\n    assert True\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [test_file])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(
        main, "load_dynamic_coverage_results", lambda p: {"mod.py": {"f": 1}}
    )

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {"mod.py": {"f"}}


def test_dynamic_coverage_with_coverage_json(tmp_path, monkeypatch):
    """_run_dynamic_coverage_phase handles existing coverage JSON."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> int:\n    return 1\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {}


def test_process_file_with_covered_functions(tmp_path, monkeypatch):
    """_process_file mutation-tests covered functions."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> bool:\n    return True\n")
    backup = tmp_path / "backup"
    backup.write_text(src.read_text())

    func = FunctionInfo(
        name="f",
        class_name=None,
        file_path=src,
        line_number=1,
        body_start_line=2,
        end_line=2,
        body_col_offset=4,
        return_type="bool",
    )

    monkeypatch.setattr(main, "find_functions", lambda fp: [func])
    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _ok_agg())
    monkeypatch.setattr(main, "backup_file", lambda fp: backup)
    monkeypatch.setattr(main, "restore_file", lambda fp, bp: None)
    monkeypatch.setattr(main, "cleanup_backup", lambda bp: None)

    entry = main._process_file(
        src, tmp_path, "pytest", 1, MagicMock(), executed_function_keys={"f"}
    )
    assert entry is not None
    funcs = entry["functions"]
    assert len(funcs) == 1
    assert funcs[0]["covered"] is True
    assert len(funcs[0]["mutants"]) == 2


def test_process_file_skips_uncovered(tmp_path, monkeypatch):
    """_process_file skips uncovered functions and marks them covered=False."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> int:\n    return 1\n\ndef g() -> str:\n    return 'x'\n")
    backup = tmp_path / "backup"
    backup.write_text(src.read_text())

    f_info = FunctionInfo(
        name="f",
        class_name=None,
        file_path=src,
        line_number=1,
        body_start_line=2,
        end_line=2,
        body_col_offset=4,
        return_type="int",
    )
    g_info = FunctionInfo(
        name="g",
        class_name=None,
        file_path=src,
        line_number=3,
        body_start_line=4,
        end_line=4,
        body_col_offset=4,
        return_type="str",
    )

    monkeypatch.setattr(main, "find_functions", lambda fp: [f_info, g_info])
    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _ok_agg())
    monkeypatch.setattr(main, "backup_file", lambda fp: backup)
    monkeypatch.setattr(main, "restore_file", lambda fp, bp: None)
    monkeypatch.setattr(main, "cleanup_backup", lambda bp: None)

    entry = main._process_file(
        src, tmp_path, "pytest", 1, MagicMock(), executed_function_keys={"f"}
    )
    funcs = entry["functions"]
    covered = next(f for f in funcs if f["function_name"] == "f")
    uncovered = next(f for f in funcs if f["function_name"] == "g")
    assert covered["covered"] is True
    assert len(covered["mutants"]) == 2
    assert uncovered["covered"] is False
    assert uncovered["mutants"] == []


def test_process_function_runs_mutants(tmp_path, monkeypatch):
    """_process_function generates and runs all mutants."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> int:\n    return 1\n")
    backup = tmp_path / "backup"
    backup.write_text(src.read_text())

    func = FunctionInfo(
        name="f",
        class_name=None,
        file_path=src,
        line_number=1,
        body_start_line=2,
        end_line=2,
        body_col_offset=4,
        return_type="int",
    )

    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _ok_agg())
    monkeypatch.setattr(main, "backup_file", lambda fp: backup)
    monkeypatch.setattr(main, "restore_file", lambda fp, bp: None)
    monkeypatch.setattr(main, "cleanup_backup", lambda bp: None)

    entry = main._process_function(func, "pytest", 1, tmp_path, MagicMock())
    assert entry["function_name"] == "f"
    assert entry["covered"] is True
    assert len(entry["mutants"]) == 2
    assert entry["mutants"][0]["exit_code"] == 0


def test_run_single_mutant_killed(tmp_path, monkeypatch):
    """_run_single_mutant applies mutation, runs tests, restores, returns KILLED."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def add(a: int, b: int) -> int:\n    return a + b\n")
    backup = tmp_path / "backup"
    backup.write_text(src.read_text())

    func = FunctionInfo(
        name="add",
        class_name=None,
        file_path=src,
        line_number=1,
        body_start_line=2,
        end_line=2,
        body_col_offset=4,
        return_type="int",
    )

    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _fail_agg(1))

    result, status, verification, codes = main._run_single_mutant(
        src, func, "return 0", "pytest", 1, tmp_path, backup
    )
    assert status == "KILLED"
    assert result.exit_code == 1
    assert verification["mutation_applied"] is True
    assert verification["mutation_restored"] is True
    assert codes == [1]


def test_run_single_mutant_survived(tmp_path, monkeypatch):
    """_run_single_mutant returns SURVIVED when tests pass."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> int:\n    return 42\n")
    backup = tmp_path / "backup"
    backup.write_text(src.read_text())

    func = FunctionInfo(
        name="f",
        class_name=None,
        file_path=src,
        line_number=1,
        body_start_line=2,
        end_line=2,
        body_col_offset=4,
        return_type="int",
    )

    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _ok_agg())

    result, status, _, _ = main._run_single_mutant(
        src, func, "return 0", "pytest", 1, tmp_path, backup
    )
    assert status == "SURVIVED"
    assert result.exit_code == 0
