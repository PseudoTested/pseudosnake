"""Tests for pseudosnake.runner — test suite execution."""

from pathlib import Path

from pseudosnake.runner import (
    AggregateRunResult,
    RunResult,
    aggregate_run_results,
    classify_result,
    extract_failed_tests,
    extract_pytest_counts,
    run_tests,
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
    result = run_tests("python -c 'raise SystemExit(0)'", tmp_path)
    assert result.exit_code == 0


def test_run_tests_captures_nonzero_exit(tmp_path: Path) -> None:
    """run_tests captures non-zero exit codes correctly."""
    result = run_tests("python -c 'raise SystemExit(42)'", tmp_path)
    assert result.exit_code == 42


def test_run_tests_records_duration(tmp_path: Path) -> None:
    """run_tests records a positive duration."""
    result = run_tests("python -c 'pass'", tmp_path)
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
