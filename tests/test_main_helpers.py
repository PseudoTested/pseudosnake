"""Tests for pure helper functions in pseudosnake.main."""

from datetime import datetime, timezone
from pathlib import Path

import pytest

from pseudosnake.discover import FunctionInfo
from pseudosnake.main import (
    _build_metadata,
    _build_uncovered_entry,
    _digest,
    _print_mutant_result,
)


def test_digest_returns_consistent_hash() -> None:
    """_digest returns the same hash for the same content."""
    h1 = _digest("hello")
    h2 = _digest("hello")
    h3 = _digest("world")
    assert h1 == h2
    assert h1 != h3
    assert len(h1) == 64


def test_digest_empty_string() -> None:
    """_digest handles empty string."""
    result = _digest("")
    assert len(result) == 64


def test_build_metadata_basic() -> None:
    """_build_metadata returns a dict with all expected keys."""
    start = datetime(2024, 1, 1, 0, 0, 0, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, 1, 0, 0, tzinfo=timezone.utc)
    meta = _build_metadata(
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
    assert meta["project_directory"] == "/proj"
    assert meta["source_directory"] == "/proj/src"
    assert meta["file_argument"] is None
    assert meta["output_file"] == "/proj/output.json"
    assert meta["test_command"] == "pytest tests/"
    assert meta["num_test_runs"] == 3
    assert meta["dynamic_coverage_enabled"] is True
    assert meta["dynamically_executed_functions"] == 5
    assert meta["files_detected"] == 10
    assert "python_version" in meta
    assert "operating_system" in meta
    assert "backup_directory" in meta


def test_build_metadata_none_source_dir_and_file() -> None:
    """_build_metadata sets source_directory and file_argument to None."""
    start = datetime(2024, 1, 1, tzinfo=timezone.utc)
    end = datetime(2024, 1, 1, tzinfo=timezone.utc)
    meta = _build_metadata(
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
    """_build_metadata records file_argument when provided."""
    start = datetime(2024, 6, 1, tzinfo=timezone.utc)
    end = datetime(2024, 6, 1, tzinfo=timezone.utc)
    meta = _build_metadata(
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
    assert meta["file_argument"] == "/proj/src/mod.py"


def test_print_mutant_result_survived(capsys) -> None:
    """_print_mutant_result prints red for SURVIVED."""
    _print_mutant_result("my_func", "return 0", "SURVIVED")
    captured = capsys.readouterr()
    assert "SURVIVED" in captured.out
    assert "my_func" in captured.out
    assert "return 0" in captured.out


def test_print_mutant_result_killed(capsys) -> None:
    """_print_mutant_result prints green for KILLED."""
    _print_mutant_result("func", "return 1", "KILLED")
    captured = capsys.readouterr()
    assert "KILLED" in captured.out


def test_print_mutant_result_no_tests(capsys) -> None:
    """_print_mutant_result prints yellow for NO_TESTS."""
    _print_mutant_result("f", "return None", "NO_TESTS")
    captured = capsys.readouterr()
    assert "NO_TESTS" in captured.out


def test_print_mutant_result_crash(capsys) -> None:
    """_print_mutant_result prints yellow for CRASH."""
    _print_mutant_result("f", "return None", "CRASH")
    captured = capsys.readouterr()
    assert "CRASH" in captured.out


def test_print_mutant_result_unknown(capsys) -> None:
    """_print_mutant_result prints white for unknown status."""
    _print_mutant_result("f", "return None", "UNKNOWN")
    captured = capsys.readouterr()
    assert "UNKNOWN" in captured.out


def test_build_uncovered_entry_top_level() -> None:
    """_build_uncovered_entry returns covered=False with empty mutants."""
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
    entry = _build_uncovered_entry(func)
    assert entry["function_name"] == "my_func"
    assert entry["covered"] is False
    assert entry["mutants"] == []
    assert entry["return_type"] == "int"
    assert "class_name" not in entry


def test_build_uncovered_entry_with_class() -> None:
    """_build_uncovered_entry includes class_name when present."""
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
    entry = _build_uncovered_entry(func)
    assert entry["class_name"] == "MyClass"
    assert entry["covered"] is False


class TestValidateBaseline:
    """Tests for _validate_baseline using mocked runner."""

    def test_passing_baseline(self, monkeypatch) -> None:
        """_validate_baseline returns None when all test runs pass."""
        from pseudosnake.runner import AggregateRunResult, RunResult

        def mock_run_tests_repeated(cmd, proj_dir, num_runs, **kwargs):
            return AggregateRunResult(
                overall=RunResult(exit_code=0, stdout="ok", stderr="", duration=0.1),
                per_run=[RunResult(exit_code=0, stdout="ok", stderr="", duration=0.1)],
            )

        from pseudosnake import main

        monkeypatch.setattr(main, "run_tests_repeated", mock_run_tests_repeated)
        result = main._validate_baseline("pytest", Path("/tmp"), 1)
        assert result is None

    def test_failing_baseline_raises_exit(self, monkeypatch) -> None:
        """_validate_baseline raises typer.Exit(2) when tests fail."""
        from pseudosnake.runner import AggregateRunResult, RunResult

        def mock_run_tests_repeated(cmd, proj_dir, num_runs, **kwargs):
            return AggregateRunResult(
                overall=RunResult(exit_code=1, stdout="fail", stderr="", duration=0.1),
                per_run=[
                    RunResult(exit_code=1, stdout="fail", stderr="", duration=0.1)
                ],
            )

        import typer

        from pseudosnake import main

        monkeypatch.setattr(main, "run_tests_repeated", mock_run_tests_repeated)
        with pytest.raises(typer.Exit) as exc_info:
            main._validate_baseline("pytest", Path("/tmp"), 1)
        assert exc_info.value.exit_code == 2

    def test_no_tests_baseline_raises_exit(self, monkeypatch, capsys) -> None:
        """_validate_baseline prints hint when exit_code is 5."""
        from pseudosnake.runner import AggregateRunResult, RunResult

        def mock_run_tests_repeated(cmd, proj_dir, num_runs, **kwargs):
            return AggregateRunResult(
                overall=RunResult(exit_code=5, stdout="", stderr="", duration=0.1),
                per_run=[RunResult(exit_code=5, stdout="", stderr="", duration=0.1)],
            )

        import typer

        from pseudosnake import main

        monkeypatch.setattr(main, "run_tests_repeated", mock_run_tests_repeated)
        with pytest.raises(typer.Exit):
            main._validate_baseline("pytest", Path("/tmp"), 1)
        captured = capsys.readouterr()
        assert "repository root" in captured.out


class TestAnalyzeFunction:
    """Tests for the analyze command function with monkeypatching."""

    def test_analyze_produces_report(self, tmp_path, monkeypatch) -> None:
        """analyze runs the full pipeline and writes a report."""
        from pseudosnake import main

        src = tmp_path / "src"
        src.mkdir()
        (src / "mod.py").write_text(
            "def add(a: int, b: int) -> int:\n    return a + b\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_mod.py").write_text(
            "from src.mod import add\n\ndef test_add():\n    assert add(1, 2) == 3\n"
        )

        def mock_validate_baseline(test_cmd, proj_dir, num_runs, **kwargs):
            return None

        monkeypatch.setattr(main, "_validate_baseline", mock_validate_baseline)

        main.analyze(
            project_dir=tmp_path,
            test_command="python -m pytest tests/",
            file=None,
            source_dir=None,
            num_test_runs=1,
            experimental_dynamic_coverage=False,
            output=None,
        )

        report = tmp_path / "output" / "output.json"
        assert report.exists()
        import json

        data = json.loads(report.read_text())
        assert "files" in data
        assert "metadata" in data
        assert data["metadata"]["num_test_runs"] == 1

    def test_analyze_with_custom_output(self, tmp_path, monkeypatch) -> None:
        """analyze with --output writes to custom path."""
        from pseudosnake import main

        (tmp_path / "mod.py").write_text("def greet() -> str:\n    return 'hello'\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_mod.py").write_text(
            "from mod import greet\n\ndef test_greet():\n    assert greet() == 'hello'\n"
        )

        def mock_validate_baseline(test_cmd, proj_dir, num_runs, **kwargs):
            return None

        monkeypatch.setattr(main, "_validate_baseline", mock_validate_baseline)
        custom = tmp_path / "custom.json"

        main.analyze(
            project_dir=tmp_path,
            test_command="python -m pytest tests/",
            file=None,
            source_dir=None,
            num_test_runs=1,
            experimental_dynamic_coverage=False,
            output=custom,
        )

        assert custom.exists()
        import json

        data = json.loads(custom.read_text())
        assert "files" in data

    def test_analyze_empty_project_no_functions(self, tmp_path, monkeypatch) -> None:
        """analyze handles projects with no functions gracefully."""
        from pseudosnake import main

        (tmp_path / "empty.py").write_text("# no functions here\nx = 1\n")
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_nothing.py").write_text(
            "def test_truth():\n    assert True\n"
        )

        def mock_validate_baseline(test_cmd, proj_dir, num_runs, **kwargs):
            return None

        monkeypatch.setattr(main, "_validate_baseline", mock_validate_baseline)

        main.analyze(
            project_dir=tmp_path,
            test_command="python -m pytest tests/",
            file=None,
            source_dir=None,
            num_test_runs=1,
            experimental_dynamic_coverage=False,
            output=None,
        )

        report = tmp_path / "output" / "output.json"
        assert report.exists()
        import json

        data = json.loads(report.read_text())
        assert data["files"] == []

    def test_analyze_filters_uncovered_functions(self, tmp_path, monkeypatch) -> None:
        """analyze skips mutation for functions not executed during dynamic coverage."""
        from pseudosnake import main

        (tmp_path / "mod.py").write_text(
            "def used() -> int:\n    return 1\n\ndef unused() -> str:\n    return 'x'\n"
        )
        tests_dir = tmp_path / "tests"
        tests_dir.mkdir()
        (tests_dir / "__init__.py").write_text("")
        (tests_dir / "test_mod.py").write_text(
            "from mod import used\n\ndef test_used():\n    assert used() == 1\n"
        )

        def mock_validate_baseline(test_cmd, proj_dir, num_runs, **kwargs):
            return None

        def mock_dynamic_coverage_phase(files, proj_dir, test_cmd):
            return {"mod.py": {"used"}}

        monkeypatch.setattr(main, "_validate_baseline", mock_validate_baseline)
        monkeypatch.setattr(
            main, "_run_dynamic_coverage_phase", mock_dynamic_coverage_phase
        )

        main.analyze(
            project_dir=tmp_path,
            test_command="python -m pytest tests/",
            file=None,
            source_dir=None,
            num_test_runs=1,
            experimental_dynamic_coverage=True,
            output=None,
        )

        report = tmp_path / "output" / "output.json"
        import json

        data = json.loads(report.read_text())
        assert data["metadata"]["dynamically_executed_functions"] == 1
        functions = data["files"][0]["functions"]
        assert len(functions) == 2
        used_entry = next(f for f in functions if f["function_name"] == "used")
        unused_entry = next(f for f in functions if f["function_name"] == "unused")
        assert used_entry["covered"] is True
        assert unused_entry["covered"] is False
        assert unused_entry["mutants"] == []
        assert len(used_entry["mutants"]) >= 1
