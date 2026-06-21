"""Tests for experimental dynamic coverage instrumentation helpers."""

from pathlib import Path

from pseudosnake.discover import find_functions
from pseudosnake.dynamic_coverage import (
    collect_executed_function_keys,
    instrument_file_source,
    load_dynamic_coverage_results,
)


def test_instrument_file_source_inserts_counter_and_header(tmp_path: Path) -> None:
    """Instrumentation inserts module header and function execution counter."""
    source = "def add(a, b):\n    return a + b\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")
    functions = find_functions(target)

    instrumented = instrument_file_source(source, functions, "mod.py")

    assert "__pseudosnake_cov__" in instrumented
    assert "__pseudosnake_flush_cov__" in instrumented
    assert "add" in instrumented


def test_load_dynamic_coverage_results_handles_missing_file(tmp_path: Path) -> None:
    """Loading results returns empty mapping when file is absent."""
    result = load_dynamic_coverage_results(tmp_path / "missing.json")
    assert result == {}


def test_collect_executed_function_keys_filters_by_positive_count(
    tmp_path: Path,
) -> None:
    """Executed function keys are selected from coverage results for one file."""
    source = "def a():\n    return 1\n\n\ndef b():\n    return 2\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")
    functions = find_functions(target)

    coverage = {
        "mod.py": {
            "a": 3,
            "b": 0,
        }
    }

    executed = collect_executed_function_keys(target, tmp_path, functions, coverage)
    assert executed == ["a"]
