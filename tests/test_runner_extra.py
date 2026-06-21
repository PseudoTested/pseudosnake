"""Tests for pseudosnake.runner — additional edge cases."""

from pathlib import Path

from pseudosnake.runner import (
    aggregate_run_results,
    extract_failed_tests,
    extract_pytest_counts,
    run_tests,
    run_tests_with_env,
    RunResult,
)


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
