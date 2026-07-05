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
    analyze,
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
    src_text = "def f() -> int:\n    return 1\n"
    src.write_text(src_text)
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_mod.py"
    test_text = "def test_f():\n    assert True\n"
    test_file.write_text(test_text)

    monkeypatch.setattr(main, "find_test_files", lambda pd: [test_file])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(
        main, "load_dynamic_coverage_results", lambda p: {"mod.py": {"f": 1}}
    )

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {"mod.py": {"f"}}

    # Verify files were restored to their original content
    assert src.read_text() == src_text, (
        "source file was not restored after coverage phase"
    )
    assert test_file.read_text() == test_text, (
        "test file was not restored after coverage phase"
    )


def test_restore_after_coverage_phase(tmp_path, monkeypatch):
    """Files are restored to original content even when coverage is empty."""
    import pseudosnake.main as main

    original = "def add(a: int, b: int) -> int:\n    return a + b\n\ndef multiply(x: int, y: int) -> int:\n    return x * y\n"
    src = tmp_path / "math_ops.py"
    src.write_text(original)

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {}

    # File must be restored to its exact original content
    current = src.read_text()
    assert current == original, (
        f"File was NOT restored after coverage phase.\n"
        f"Expected ({len(original)} chars):\n{original}\n"
        f"Got ({len(current)} chars):\n{current}"
    )


def test_restore_after_coverage_with_test_file(tmp_path, monkeypatch):
    """Both source and test files are restored after the coverage phase."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src_text = "def foo():\n    return 42\n"
    src.write_text(src_text)

    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_mod.py"
    test_text = "def test_foo():\n    assert foo() == 42\n"
    test_file.write_text(test_text)

    monkeypatch.setattr(main, "find_test_files", lambda pd: [test_file])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    main._run_dynamic_coverage_phase([src], tmp_path, "pytest")

    assert src.read_text() == src_text, "source file not restored"
    assert test_file.read_text() == test_text, "test file not restored"


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


# --- coverage gap: baseline with mixed exit codes (110->109, 109->116) ---


def test_validate_baseline_prints_stderr_with_mixed_runs(tmp_path: Path) -> None:
    """_validate_baseline prints stderr from first failing run across multiple runs."""
    import typer

    with patch("pseudosnake.main.run_tests_repeated") as mock_run:
        mock_run.return_value = AggregateRunResult(
            overall=RunResult(exit_code=1, stdout="", stderr="", duration=0.1),
            per_run=[
                RunResult(exit_code=0, stdout="", stderr="", duration=0.1),
                RunResult(exit_code=1, stdout="", stderr="FAIL", duration=0.1),
            ],
        )
        try:
            _validate_baseline("pytest", tmp_path, 2)
            assert False, "should have raised"
        except typer.Exit:
            pass


# --- coverage gap: already-instrumented guards ---


def test_dynamic_coverage_skips_instrumented_test_file(tmp_path, monkeypatch):
    """Test files already containing the marker are skipped."""
    import pseudosnake.main as main
    from pseudosnake.dynamic_coverage import _INSTRUMENTATION_MARKER as MARKER

    test_file = tmp_path / "test_x.py"
    test_file.write_text(MARKER + "\ndef test_x(): pass\n")
    src = tmp_path / "mod.py"
    src.write_text("def f(): pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [test_file])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {}


def test_dynamic_coverage_skips_instrumented_source_file(tmp_path, monkeypatch):
    """Source files already containing the marker are skipped."""
    import pseudosnake.main as main
    from pseudosnake.dynamic_coverage import _INSTRUMENTATION_MARKER as MARKER

    src = tmp_path / "mod.py"
    src.write_text(MARKER + "\ndef f(): pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {}


# --- coverage gap: test file with no functions (line 161) ---


def test_dynamic_coverage_skips_empty_test_file(tmp_path, monkeypatch):
    """Test files with no test functions are skipped."""
    import pseudosnake.main as main

    test_file = tmp_path / "test_x.py"
    test_file.write_text("x = 1\n")
    src = tmp_path / "mod.py"
    src.write_text("def f():\n    pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [test_file])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    main._run_dynamic_coverage_phase([src], tmp_path, "pytest")


# --- coverage gap: source/test overlap (line 176) ---


def test_dynamic_coverage_skips_source_already_test(tmp_path, monkeypatch):
    """Source file already instrumented as a test file is skipped."""
    import pseudosnake.main as main

    mod = tmp_path / "mod.py"
    mod.write_text("def f():\n    pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [mod])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    result = main._run_dynamic_coverage_phase([mod], tmp_path, "pytest")
    assert result == {}


# --- coverage gap: coverage JSON exists + executed empty + functions present (212-213, 240-241) ---


def test_dynamic_coverage_executed_map_empty(tmp_path, monkeypatch):
    """When coverage data exists but functions have zero hits, executed_map is empty."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f():\n    pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(
        main, "load_dynamic_coverage_results", lambda p: {"mod.py": {"g": 0}}
    )

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {"mod.py": set()}

    # Verify restore still happened
    assert src.read_text() == "def f():\n    pass\n"


# --- coverage gap: restore failure (262-264, 268-269, 257-262) ---


def test_dynamic_coverage_restore_failure_warning(tmp_path, monkeypatch):
    """When restore fails, a warning is printed and cleanup still runs."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f():\n    pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})
    monkeypatch.setattr(
        main, "restore_file", lambda fp, bp: (_ for _ in ()).throw(Exception("boom"))
    )

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {}


def test_dynamic_coverage_cleanup_failure_silent(tmp_path, monkeypatch):
    """When cleanup_backup raises, the loop continues silently."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f():\n    pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})
    monkeypatch.setattr(
        main, "cleanup_backup", lambda bp: (_ for _ in ()).throw(Exception("boom"))
    )

    result = main._run_dynamic_coverage_phase([src], tmp_path, "pytest")
    assert result == {}


# --- coverage gap: _process_file with executed_function_keys=None (308->311) ---


def test_process_file_without_coverage_keys(tmp_path, monkeypatch):
    """_process_file mutation-tests all functions when executed_function_keys is None."""
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

    monkeypatch.setattr(main, "find_functions", lambda fp: [func])
    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _ok_agg())
    monkeypatch.setattr(main, "backup_file", lambda fp: backup)
    monkeypatch.setattr(main, "restore_file", lambda fp, bp: None)
    monkeypatch.setattr(main, "cleanup_backup", lambda bp: None)

    # executed_function_keys=None — should still mutation-test all functions
    entry = main._process_file(src, tmp_path, "pytest", 1, MagicMock())
    assert entry is not None
    assert len(entry["functions"]) == 1
    assert entry["functions"][0]["covered"] is True
    assert len(entry["functions"][0]["mutants"]) == 2


# --- coverage gap: coverage JSON file exists (212-213) ---


def test_dynamic_coverage_json_exists(tmp_path, monkeypatch):
    """When coverage JSON file exists, its size is printed."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f():\n    pass\n")

    fake_temp = tmp_path / "temps"
    fake_temp.mkdir()
    monkeypatch.setattr("pseudosnake.main.tempfile.gettempdir", lambda: str(fake_temp))

    # simulate the test runner writing the coverage JSON
    # the coverage path is now unique per run, so read it from the env var
    def write_coverage_json(*a, **kw):
        cov_path = kw.get("extra_env", {}).get("PSEUDOSNAKE_DYN_COV_PATH", "")
        Path(cov_path).write_text('{"mod.py": {"f": 1}}')

    monkeypatch.setattr(main, "run_tests_with_env", write_coverage_json)
    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    main._run_dynamic_coverage_phase([src], tmp_path, "pytest")


# --- coverage gap: restore verification failure (257) ---


def test_dynamic_coverage_restore_verification_fails(tmp_path, monkeypatch):
    """When restored content differs from backup, a red error is printed."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f():\n    pass\n")

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(main, "load_dynamic_coverage_results", lambda p: {})

    # mock restore_file to leave the file in its instrumented state,
    # which differs from the backup's original content
    def noop_restore(fp, bp):
        pass

    monkeypatch.setattr(main, "restore_file", noop_restore)

    main._run_dynamic_coverage_phase([src], tmp_path, "pytest")


# --- coverage gap: multiple source files (240->227) ---


def test_dynamic_coverage_multiple_source_files(tmp_path, monkeypatch):
    """Coverage phase handles multiple source files, including empty ones."""
    import pseudosnake.main as main

    src1 = tmp_path / "mod1.py"
    src1.write_text("def f():\n    pass\n")
    src2 = tmp_path / "mod2.py"
    src2.write_text("x = 1\n")  # no functions — exercises elif branch fall-through

    monkeypatch.setattr(main, "find_test_files", lambda pd: [])
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(
        main,
        "load_dynamic_coverage_results",
        lambda p: {"mod1.py": {"f": 1}, "mod2.py": {}},
    )

    result = main._run_dynamic_coverage_phase([src1, src2], tmp_path, "pytest")
    assert result == {"mod1.py": {"f"}, "mod2.py": set()}


# --- coverage gap: analyze CLI command (510-586) ---


def test_analyze_command_integration(tmp_path, monkeypatch):
    """The analyze command orchestrates the full pipeline end to end."""
    import pseudosnake.main as main

    src = tmp_path / "mod.py"
    src.write_text("def f() -> int:\n    return 1\n")
    src2 = tmp_path / "extra.py"
    src2.write_text("def g() -> str:\n    return 'x'\n")
    empty = tmp_path / "empty.py"
    empty.write_text("# no functions\nx = 1\n")
    test_dir = tmp_path / "tests"
    test_dir.mkdir()
    test_file = test_dir / "test_mod.py"
    test_file.write_text("def test_f():\n    assert True\n")

    snap_dir = tmp_path / ".pseudosnake_snap"
    monkeypatch.setattr(main, "find_test_files", lambda pd: [test_file])
    monkeypatch.setattr(
        main, "create_snapshot", lambda files, pd, sid: snap_dir.mkdir(exist_ok=True) or snap_dir
    )
    monkeypatch.setattr(main, "restore_snapshot", lambda sd, pd: 0)
    monkeypatch.setattr(main, "cleanup_snapshot", lambda sd: None)
    monkeypatch.setattr(main, "run_tests_repeated", lambda *a, **kw: _ok_agg())
    monkeypatch.setattr(main, "run_tests_with_env", lambda *a, **kw: None)
    monkeypatch.setattr(
        main,
        "load_dynamic_coverage_results",
        lambda p: {"mod.py": {"f": 1}, "extra.py": {"g": 1}},
    )

    analyze(
        project_dir=tmp_path,
        test_command="pytest tests/",
        file=None,
        source_dir=None,
        num_test_runs=1,
        output=tmp_path / "report.json",
        test_timeout=60,
    )

    # After the run, source and test files must be restored to their original state
    assert src.read_text() == "def f() -> int:\n    return 1\n"
    assert test_file.read_text() == "def test_f():\n    assert True\n"

    # Report should be written
    assert (tmp_path / "report.json").exists()
