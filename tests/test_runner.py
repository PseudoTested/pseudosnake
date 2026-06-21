"""Tests for pseudosnake.runner — test suite execution."""

import subprocess
from pathlib import Path
from unittest.mock import Mock, patch

from pseudosnake.runner import (
    AggregateRunResult,
    RunResult,
    aggregate_run_results,
    classify_result,
    extract_failed_tests,
    extract_pytest_counts,
    run_tests,
    run_tests_with_env,
)


def test_classify_result_survived() -> None:
    """classify_result returns SURVIVED when exit code is 0."""
    result = RunResult(exit_code=0, stdout="", stderr="", duration=0.1)
    assert classify_result(result) == "SURVIVED"


def test_classify_result_killed() -> None:
    """classify_result returns KILLED when exit code is non-zero."""
    result = RunResult(exit_code=1, stdout="", stderr="FAILED", duration=0.1)
    assert classify_result(result) == "KILLED"


def test_classify_result_no_tests() -> None:
    """classify_result returns NO_TESTS for pytest exit code 5."""
    result = RunResult(exit_code=5, stdout="", stderr="", duration=0.1)
    assert classify_result(result) == "NO_TESTS"


def test_classify_result_crash() -> None:
    """classify_result returns CRASH when exit code is -1."""
    result = RunResult(exit_code=-1, stdout="", stderr="error", duration=0.0)
    assert classify_result(result) == "CRASH"


def test_run_tests_captures_exit_code(tmp_path: Path) -> None:
    """run_tests returns the actual exit code from the subprocess."""
    result = run_tests('python -c "raise SystemExit(0)"', tmp_path)
    assert result.exit_code == 0


def test_run_tests_captures_nonzero_exit(tmp_path: Path) -> None:
    """run_tests captures non-zero exit codes correctly."""
    result = run_tests('python -c "raise SystemExit(42)"', tmp_path)
    assert result.exit_code == 42


def test_run_tests_records_duration(tmp_path: Path) -> None:
    """run_tests records a positive duration."""
    result = run_tests('python -c "pass"', tmp_path)
    assert result.duration >= 0.0


def test_extract_failed_tests_parses_pytest_summary_lines() -> None:
    """extract_failed_tests parses FAILED node ids from output."""
    output = (
        "=========================== short test summary info ===========================\n"
        "FAILED tests/test_a.py::test_one - AssertionError\n"
        "FAILED tests/test_b.py::test_two - ValueError\n"
    )
    result = RunResult(exit_code=1, stdout=output, stderr="", duration=0.1)
    assert extract_failed_tests(result) == [
        "tests/test_a.py::test_one",
        "tests/test_b.py::test_two",
    ]


def test_extract_pytest_counts_parses_failed_and_passed_counts() -> None:
    """extract_pytest_counts reads counts from summary fragments."""
    output = "=================== 2 failed, 83 passed in 1.50s ==================="
    result = RunResult(exit_code=1, stdout=output, stderr="", duration=0.1)
    counts = extract_pytest_counts(result)
    assert counts["failed"] == 2
    assert counts["passed"] == 83


def test_aggregate_run_results_all_pass() -> None:
    """aggregate_run_results returns 0 when every run passes."""
    per_run = [
        RunResult(exit_code=0, stdout="ok", stderr="", duration=0.1),
        RunResult(exit_code=0, stdout="ok", stderr="", duration=0.2),
    ]
    aggregate = aggregate_run_results(per_run)
    assert isinstance(aggregate, AggregateRunResult)
    assert aggregate.overall.exit_code == 0
    assert abs(aggregate.overall.duration - 0.3) < 1e-9


def test_aggregate_run_results_first_nonzero_exit_code() -> None:
    """aggregate_run_results picks first non-zero exit code as overall exit."""
    per_run = [
        RunResult(exit_code=0, stdout="ok", stderr="", duration=0.1),
        RunResult(exit_code=5, stdout="", stderr="", duration=0.1),
        RunResult(exit_code=1, stdout="", stderr="", duration=0.1),
    ]
    aggregate = aggregate_run_results(per_run)
    assert aggregate.overall.exit_code == 5


def test_aggregate_run_results_empty_list() -> None:
    """aggregate_run_results handles an empty list gracefully."""
    result = aggregate_run_results([])
    assert result.overall.exit_code == -1
    assert result.overall.stderr == "no runs executed"
    assert result.per_run == []


def test_extract_failed_tests_no_failures() -> None:
    """extract_failed_tests returns empty list when no FAILED lines present."""
    result = RunResult(exit_code=0, stdout="all passed", stderr="", duration=0.1)
    assert extract_failed_tests(result) == []


def test_extract_failed_tests_deduplicates() -> None:
    """extract_failed_tests removes duplicate entries from repeated runs."""
    stdout = (
        "FAILED tests/a.py::test_x - E\n"
        "FAILED tests/a.py::test_x - E\n"
        "FAILED tests/b.py::test_y - E\n"
    )
    result = RunResult(exit_code=1, stdout=stdout, stderr="", duration=0.1)
    assert extract_failed_tests(result) == ["tests/a.py::test_x", "tests/b.py::test_y"]


def test_extract_failed_tests_from_stderr() -> None:
    """extract_failed_tests also scans stderr lines."""
    result = RunResult(
        exit_code=1,
        stdout="",
        stderr="FAILED tests/c.py::test_z - AssertionError\n",
        duration=0.1,
    )
    assert extract_failed_tests(result) == ["tests/c.py::test_z"]


def test_extract_pytest_counts_only_failed() -> None:
    """extract_pytest_counts handles output with only failed count."""
    result = RunResult(
        exit_code=1,
        stdout="1 failed in 0.50s\n",
        stderr="",
        duration=0.1,
    )
    counts = extract_pytest_counts(result)
    assert counts["failed"] == 1
    assert counts["passed"] == 0


def test_extract_pytest_counts_only_passed() -> None:
    """extract_pytest_counts handles output with only passed count."""
    result = RunResult(
        exit_code=0,
        stdout="85 passed in 1.00s\n",
        stderr="",
        duration=0.1,
    )
    counts = extract_pytest_counts(result)
    assert counts["failed"] == 0
    assert counts["passed"] == 85


def test_extract_pytest_counts_no_counts() -> None:
    """extract_pytest_counts returns zeros when no count info is present."""
    result = RunResult(exit_code=0, stdout="", stderr="", duration=0.1)
    counts = extract_pytest_counts(result)
    assert counts == {"failed": 0, "passed": 0}


def test_run_tests_with_env_passes_env_vars(tmp_path: Path) -> None:
    """run_tests_with_env passes extra environment variables to the subprocess."""

    script = tmp_path / "check_env.py"
    script.write_text(
        "import os\nprint(os.environ.get('PSEUDOSNAKE_TEST_VAR', 'NOT_SET'))\n"
    )
    result = run_tests_with_env(
        f"python {script}",
        tmp_path,
        extra_env={"PSEUDOSNAKE_TEST_VAR": "hello"},
    )
    assert result.exit_code == 0
    assert "hello" in result.stdout
    assert "NOT_SET" not in result.stdout


def test_run_tests_with_env_no_extra_env(tmp_path: Path) -> None:
    """run_tests_with_env works with None extra_env."""
    result = run_tests_with_env('python -c "print(1)"', tmp_path)
    assert result.exit_code == 0
    assert "1" in result.stdout


def test_aggregate_run_results_all_nonzero() -> None:
    """aggregate_run_results picks the first non-zero when all fail."""
    per_run = [
        RunResult(exit_code=2, stdout="", stderr="", duration=0.1),
        RunResult(exit_code=3, stdout="", stderr="", duration=0.1),
    ]
    result = aggregate_run_results(per_run)
    assert result.overall.exit_code == 2


def test_run_tests_handles_invalid_cwd() -> None:
    """run_tests returns CRASH result when cwd doesn't exist."""
    result = run_tests("echo hi", Path("/definitely/not/a/real/path"))
    assert result.exit_code == -1
    assert result.stderr != ""


def test_run_tests_with_env_handles_invalid_cwd() -> None:
    """run_tests_with_env returns CRASH when cwd doesn't exist."""
    result = run_tests_with_env("echo hi", Path("/nonexistent/dir"))
    assert result.exit_code == -1


def test_run_tests_handles_timeout() -> None:
    """run_tests returns exit code -1 when subprocess times out."""
    with patch("pseudosnake.runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)
        result = run_tests("sleep 1000", Path("/tmp"), timeout=1)
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()
        assert result.duration >= 0


def test_run_tests_handles_general_exception(tmp_path: Path) -> None:
    """run_tests returns exit code -1 when subprocess raises any exception."""
    with patch("pseudosnake.runner.subprocess.run") as mock_run:
        mock_run.side_effect = FileNotFoundError("test")
        result = run_tests("nonexistent_command", tmp_path, timeout=5)
        assert result.exit_code == -1
        assert "test" in result.stderr or result.stderr != ""
        assert result.duration >= 0


def test_run_tests_with_env_handles_timeout() -> None:
    """run_tests_with_env returns exit code -1 when subprocess times out."""
    with patch("pseudosnake.runner.subprocess.run") as mock_run:
        mock_run.side_effect = subprocess.TimeoutExpired("cmd", 5)
        result = run_tests_with_env(
            "sleep 1000",
            Path("/tmp"),
            extra_env={"TEST_VAR": "value"},
            timeout=1,
        )
        assert result.exit_code == -1
        assert "timed out" in result.stderr.lower()


def test_run_tests_with_env_handles_exception() -> None:
    """run_tests_with_env returns exit code -1 when subprocess raises exception."""
    with patch("pseudosnake.runner.subprocess.run") as mock_run:
        mock_run.side_effect = RuntimeError("test error")
        result = run_tests_with_env(
            "cmd",
            Path("/tmp"),
            extra_env={"TEST_VAR": "value"},
        )
        assert result.exit_code == -1
        assert "test error" in result.stderr


def test_run_tests_preserves_captured_output(tmp_path: Path) -> None:
    """run_tests captures and returns stdout/stderr from subprocess."""
    with patch("pseudosnake.runner.subprocess.run") as mock_run:
        mock_result = Mock()
        mock_result.returncode = 0
        mock_result.stdout = "Output line 1\nOutput line 2"
        mock_result.stderr = "Error output"
        mock_run.return_value = mock_result
        result = run_tests("echo test", tmp_path)
        assert result.exit_code == 0
        assert result.stdout == "Output line 1\nOutput line 2"
        assert result.stderr == "Error output"
