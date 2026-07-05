"""JSON report assembly and output for pseudo-test analysis results."""

import json
import sys
from pathlib import Path
from typing import Any

from pseudosnake.discover import FunctionInfo
from pseudosnake.runner import RunResult, extract_failed_tests, extract_pytest_counts


# mutant-level entry


def build_mutant_entry(
    mutation: str,
    test_result: RunResult,
    status: str,
    verification: dict[str, Any] | None = None,
    run_exit_codes: list[int] | None = None,
    num_test_runs: int | None = None,
) -> dict[str, Any]:
    """Build the dict representing one mutant's test outcome."""
    # parse the test output for failed test names and pass/fail counts
    failed_tests = extract_failed_tests(test_result)
    counts = extract_pytest_counts(test_result)

    # build the core entry
    entry: dict[str, Any] = {
        "mutation": mutation,
        "exit_code": test_result.exit_code,
        "status": status,
        "pseudo_tested": status
        == "SURVIVED",  # true when tests didn't catch the mutation
        "duration": round(test_result.duration, 3),
        "failed_tests": failed_tests,  # list of pytest node ids that failed
        "failed_test_count": len(failed_tests),
        "pytest_failed_count": counts["failed"],
        "pytest_passed_count": counts["passed"],
    }
    # optional fields — only included when provided
    if run_exit_codes is not None:
        entry["run_exit_codes"] = run_exit_codes  # per-run exit codes across repeats
    if num_test_runs is not None:
        entry["num_test_runs"] = num_test_runs
    if verification is not None:
        entry["verification"] = (
            verification  # sha256 hashes proving mutation was applied
        )
    return entry


# function-level entry


def build_function_entry(
    func_info: FunctionInfo,
    mutant_entries: list[dict[str, Any]],
    covered: bool = True,
) -> dict[str, Any]:
    """Build the dict representing one function and all its mutant results."""
    entry: dict[str, Any] = {
        "function_name": func_info.name,
        "line_number": func_info.line_number,
        "return_type": func_info.return_type,
        "covered": covered,  # whether dynamic coverage confirmed this function ran
        "mutants": mutant_entries,
    }
    # only include class_name for methods (not top-level functions)
    if func_info.class_name is not None:
        entry["class_name"] = func_info.class_name
    return entry


# file & top-level report assembly


def build_file_entry(
    file_path: Path,
    project_dir: Path,
    function_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build the dict representing one source file and its analysed functions."""
    # store the relative path so reports are portable between machines
    return {
        "file": str(file_path.relative_to(project_dir)),
        "functions": function_entries,
    }


def compute_mutation_score(
    file_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Count mutants by status across all file entries and compute the mutation score.

    Mutation score = killed / (killed + survived), i.e. the fraction of
    testable mutants that were actually caught by the test suite.
    NO_TESTS and CRASH mutants are tracked but excluded from the denominator.
    Returns a dict with per-status counts and the computed score.
    """
    counts = {"KILLED": 0, "SURVIVED": 0, "NO_TESTS": 0, "CRASH": 0}
    for file_entry in file_entries:
        for func_entry in file_entry.get("functions", []):
            for mutant_entry in func_entry.get("mutants", []):
                status = mutant_entry.get("status", "")
                if status in counts:
                    counts[status] += 1
    total = sum(counts.values())
    testable = counts["KILLED"] + counts["SURVIVED"]
    score = round(counts["KILLED"] / testable * 100, 2) if testable > 0 else 0.0
    return {
        "total_mutants": total,
        "testable_mutants": testable,
        **counts,
        "mutation_score": score,
    }


def build_report(
    metadata: dict[str, Any],
    file_entries: list[dict[str, Any]],
) -> dict[str, Any]:
    """Assemble the top-level report dictionary."""
    return {
        "metadata": metadata,
        "files": file_entries,
        "mutation_score_summary": compute_mutation_score(file_entries),
    }


def output_report(report: dict[str, Any], output_file: Path | None) -> None:
    """Write the report as formatted JSON to a file, or stdout if None."""
    # serialize with indentation for readability
    content = json.dumps(report, indent=2)
    if output_file is None:
        # no file specified — write to stdout
        sys.stdout.write(content + "\n")
    else:
        # write to the specified file
        output_file.write_text(content, encoding="utf-8")
