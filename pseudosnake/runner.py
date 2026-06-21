"""Test suite execution via subprocess.

Every test run is executed as a separate subprocess so that PseudoSnake can
mutate source files on disk and re-run the project's own test command without
interference.  A *timeout* prevents hung processes from blocking the pipeline.
"""

import re
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path


# result types


@dataclass
class RunResult:
    """Captured output from a single test-suite invocation."""

    exit_code: int
    stdout: str
    stderr: str
    duration: float  # wall-clock time in seconds


@dataclass
class AggregateRunResult:
    """Aggregate information across repeated runs of the same command.

    *overall* condenses every per-run result into a single ``RunResult``.
    """

    overall: RunResult
    per_run: list[RunResult]


# subprocess wrappers


def run_tests(
    test_command: str,
    project_dir: Path,
    timeout: int | None = 300,
) -> RunResult:
    """Execute *test_command* in *project_dir* and return the captured result.

    Exit code ``-1`` means the subprocess could not be launched or timed out.
    """
    # record wall-clock start time for duration tracking
    start = time.monotonic()
    try:
        # launch the test command as a subprocess with a timeout
        proc = subprocess.run(
            test_command,
            shell=True,  # allows complex commands like "pytest tests/ -x"
            cwd=project_dir,  # run from the project's root directory
            capture_output=True,  # capture stdout and stderr for parsing
            text=True,  # return strings, not bytes
            timeout=timeout,  # kill the process if it hangs
        )
        duration = time.monotonic() - start
        return RunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        # the command took too long — treat as a crash
        return RunResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            duration=time.monotonic() - start,
        )
    except Exception as exc:
        # any other failure (file not found, permission denied, etc.)
        return RunResult(
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            duration=time.monotonic() - start,
        )


def run_tests_with_env(
    test_command: str,
    project_dir: Path,
    extra_env: dict[str, str] | None = None,
    timeout: int | None = 300,
) -> RunResult:
    """Like :func:`run_tests` but merges *extra_env* into the subprocess environment."""
    start = time.monotonic()
    try:
        # start with the current process environment, then overlay extra vars
        env = None
        if extra_env is not None:
            from os import environ

            env = dict(environ)
            env.update(extra_env)
        proc = subprocess.run(
            test_command,
            shell=True,
            cwd=project_dir,
            capture_output=True,
            text=True,
            env=env,  # pass the modified environment (or None for inherit)
            timeout=timeout,
        )
        duration = time.monotonic() - start
        return RunResult(
            exit_code=proc.returncode,
            stdout=proc.stdout,
            stderr=proc.stderr,
            duration=duration,
        )
    except subprocess.TimeoutExpired:
        return RunResult(
            exit_code=-1,
            stdout="",
            stderr=f"Command timed out after {timeout}s",
            duration=time.monotonic() - start,
        )
    except Exception as exc:
        return RunResult(
            exit_code=-1,
            stdout="",
            stderr=str(exc),
            duration=time.monotonic() - start,
        )


# repeated runs & aggregation


def run_tests_repeated(
    test_command: str,
    project_dir: Path,
    num_test_runs: int,
    timeout: int | None = 300,
) -> AggregateRunResult:
    """Run *test_command* *num_test_runs* times, returning per-run + aggregate data."""
    # execute the test command n times, collecting all results
    per_run = [
        run_tests(test_command, project_dir, timeout=timeout)
        for _ in range(num_test_runs)
    ]
    # combine the per-run results into a single aggregate
    return aggregate_run_results(per_run)


def aggregate_run_results(per_run: list[RunResult]) -> AggregateRunResult:
    """Combine per-run results into a single aggregate.

    The aggregate exit code is ``0`` only when **every** run exits ``0``.
    Otherwise it is the first non-zero code encountered.
    """
    # handle the edge case of an empty list
    if not per_run:
        empty = RunResult(
            exit_code=-1, stdout="", stderr="no runs executed", duration=0.0
        )
        return AggregateRunResult(overall=empty, per_run=[])

    # find the first non-zero exit code (or 0 if all passed)
    first_nonzero = next((r.exit_code for r in per_run if r.exit_code != 0), 0)
    # join stdout and stderr from all runs, separated by double newlines
    overall = RunResult(
        exit_code=first_nonzero,
        stdout="\n\n".join(r.stdout for r in per_run),
        stderr="\n\n".join(r.stderr for r in per_run),
        duration=sum(r.duration for r in per_run),  # total time across all runs
    )
    return AggregateRunResult(overall=overall, per_run=per_run)


# result classification & parsing


def classify_result(test_result: RunResult) -> str:
    """Map a test run's exit code to a mutation-status label.

    ============ =============================================================
    Exit code    Label
    ============ =============================================================
    ``-1``       ``CRASH`` — subprocess failed to launch or timed out.
    ``0``        ``SURVIVED`` — all tests passed *despite* the mutation.
    ``5``        ``NO_TESTS`` — pytest found no tests to collect.
    other        ``KILLED`` — at least one test failed because of the mutation.
    ============ =============================================================
    """
    # check exit codes in priority order
    if test_result.exit_code == -1:
        return "CRASH"
    if test_result.exit_code == 0:
        return "SURVIVED"
    if test_result.exit_code == 5:
        return "NO_TESTS"
    return "KILLED"


def extract_failed_tests(test_result: RunResult) -> list[str]:
    """Extract pytest ``FAILED`` test node IDs from combined stdout/stderr.

    Parses lines such as::

        FAILED tests/test_mod.py::test_name - AssertionError

    Returns a deduplicated, order-stable list.
    """
    # combine stdout and stderr into one searchable string
    combined = f"{test_result.stdout}\n{test_result.stderr}"
    # regex to match "FAILED tests/path.py::test_name" at the start of a line
    pattern = re.compile(r"^FAILED\s+([^\s]+)", re.MULTILINE)
    found = pattern.findall(combined)
    # deduplicate while preserving order (dict.fromkeys maintains insertion order)
    return list(dict.fromkeys(found))


def extract_pytest_counts(test_result: RunResult) -> dict[str, int]:
    """Extract ``failed`` / ``passed`` counts from pytest summary lines.

    Parses fragments such as ``2 failed, 83 passed in 1.50s``.
    """
    combined = f"{test_result.stdout}\n{test_result.stderr}"
    counts = {"failed": 0, "passed": 0}

    # search for patterns like "2 failed" and "83 passed"
    failed_match = re.search(r"(\d+)\s+failed", combined)
    passed_match = re.search(r"(\d+)\s+passed", combined)

    if failed_match:
        counts["failed"] = int(failed_match.group(1))
    if passed_match:
        counts["passed"] = int(passed_match.group(1))
    return counts
