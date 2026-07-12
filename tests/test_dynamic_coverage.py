"""Tests for experimental dynamic coverage instrumentation helpers."""

import json
from pathlib import Path

from pseudosnake.discover import FunctionInfo, find_functions
from pseudosnake.dynamic_coverage import (
    _build_header_block,
    _find_module_insert_index,
    collect_executed_function_keys,
    collect_test_to_function_map,
    function_key,
    instrument_file_source,
    load_dynamic_coverage_results,
)


def test_function_key_top_level() -> None:
    """function_key returns just the name for top-level functions."""
    func = FunctionInfo(
        name="my_func",
        class_name=None,
        file_path=Path("mod.py"),
        line_number=1,
        body_start_line=2,
        end_line=5,
        body_col_offset=0,
        return_type="int",
    )
    assert function_key(func) == "my_func"


def test_function_key_method() -> None:
    """function_key includes class name for methods."""
    func = FunctionInfo(
        name="my_method",
        class_name="MyClass",
        file_path=Path("mod.py"),
        line_number=10,
        body_start_line=11,
        end_line=15,
        body_col_offset=4,
        return_type="str",
    )
    assert function_key(func) == "MyClass.my_method"


def test_build_header_block_structure() -> None:
    """_build_header_block returns a string with the expected keywords."""
    header = _build_header_block("test.py")
    assert "__pseudosnake_cov__" in header
    assert "__pseudosnake_flush_cov__" in header
    assert "PSEUDOSNAKE_DYN_COV_PATH" in header
    assert "test.py" in header
    assert "atexit" in header


def test_find_module_insert_index_shebang() -> None:
    """_find_module_insert_index skips shebang line."""
    lines = ["#!/usr/bin/env python\n", "\n", "x = 1\n"]
    idx = _find_module_insert_index(lines)
    assert idx >= 1


def test_find_module_insert_index_docstring_block() -> None:
    """_find_module_insert_index skips multiline docstrings."""
    lines = [
        '"""Module docstring.\n',
        "More doc.\n",
        '"""\n',
        "x = 1\n",
    ]
    idx = _find_module_insert_index(lines)
    assert idx >= 3


def test_find_module_insert_index_future_imports() -> None:
    """_find_module_insert_index skips __future__ imports."""
    lines = [
        "from __future__ import annotations\n",
        "x = 1\n",
    ]
    idx = _find_module_insert_index(lines)
    assert idx >= 1


def test_find_module_insert_index_encoding_line() -> None:
    """_find_module_insert_index skips coding declaration."""
    lines = ["# -*- coding: utf-8 -*-\n", "x = 1\n"]
    idx = _find_module_insert_index(lines)
    assert idx == 1


def test_find_module_insert_index_single_line_docstring() -> None:
    """_find_module_insert_index skips one-line docstrings."""
    lines = ['"""Single line docstring."""\n', "x = 1\n"]
    idx = _find_module_insert_index(lines)
    assert idx == 1


def test_find_module_insert_index_multiline_no_closing_quote() -> None:
    """_find_module_insert_index handles malformed docstring gracefully."""
    lines = ['"""Unclosed docstring\n', "still doc\n", "x = 1\n"]
    idx = _find_module_insert_index(lines)
    assert idx == len(lines)


def test_find_module_insert_index_no_special_lines() -> None:
    """_find_module_insert_index starts at 0 when no special lines present."""
    lines = ["x = 1\n"]
    idx = _find_module_insert_index(lines)
    assert idx == 0


def test_find_module_insert_index_empty() -> None:
    """_find_module_insert_index handles empty source."""
    idx = _find_module_insert_index([])
    assert idx == 0


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


def test_instrument_file_source_empty_functions() -> None:
    """instrument_file_source returns source unchanged when no functions."""
    source = "x = 1\ny = 2\n"
    result = instrument_file_source(source, [], "mod.py")
    assert result == source


def test_load_dynamic_coverage_results_handles_missing_file(tmp_path: Path) -> None:
    """Loading results returns empty mapping when file is absent."""
    result = load_dynamic_coverage_results(tmp_path / "missing.json")
    assert result == {}


def test_load_dynamic_coverage_results_with_valid_json(tmp_path: Path) -> None:
    """load_dynamic_coverage_results parses valid JSON."""
    path = tmp_path / "cov.json"
    path.write_text(json.dumps({"mod.py": {"func_a": 5, "func_b": 2}}))
    result = load_dynamic_coverage_results(path)
    assert result == {"mod.py": {"func_a": 5, "func_b": 2}}


def test_load_dynamic_coverage_results_with_invalid_json(tmp_path: Path) -> None:
    """load_dynamic_coverage_results returns empty dict on invalid JSON."""
    path = tmp_path / "cov.json"
    path.write_text("{not valid json}")
    result = load_dynamic_coverage_results(path)
    assert result == {}


def test_load_dynamic_coverage_results_not_a_dict(tmp_path: Path) -> None:
    """load_dynamic_coverage_results returns empty when JSON is not a dict."""
    path = tmp_path / "cov.json"
    path.write_text("[1, 2, 3]")
    result = load_dynamic_coverage_results(path)
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


def test_collect_executed_function_keys_multiple_functions(tmp_path: Path) -> None:
    """collect_executed_function_keys returns only executed keys across functions."""
    source = "def a():\n    pass\n\ndef b():\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    functions = find_functions(target)
    coverage = {"mod.py": {"a": 1, "b": 0}}
    executed = collect_executed_function_keys(target, tmp_path, functions, coverage)
    assert executed == ["a"]


def test_collect_executed_function_keys_no_executed(tmp_path: Path) -> None:
    """collect_executed_function_keys returns empty when no function executed."""
    source = "def a():\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    functions = find_functions(target)
    coverage: dict[str, dict[str, int]] = {}
    executed = collect_executed_function_keys(target, tmp_path, functions, coverage)
    assert executed == []


def test_collect_executed_function_keys_with_nested_format(tmp_path: Path) -> None:
    """collect_executed_function_keys handles new nested caller-aware format."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        body_start_line=2,
        end_line=3,
        body_col_offset=0,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func): {"total": 5, "by_test": {"test_x": 3, "test_y": 2}},
        }
    }

    result = collect_executed_function_keys(
        source_file, tmp_path, [func], coverage_results
    )

    assert len(result) == 1
    assert function_key(func) in result


def test_collect_executed_function_keys_with_legacy_format(tmp_path: Path) -> None:
    """collect_executed_function_keys handles legacy flat format {func: int}."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        body_start_line=2,
        end_line=3,
        body_col_offset=0,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func): 10,
        }
    }

    result = collect_executed_function_keys(
        source_file, tmp_path, [func], coverage_results
    )

    assert len(result) == 1
    assert function_key(func) in result


def test_collect_executed_function_keys_zero_count_not_executed(tmp_path: Path) -> None:
    """collect_executed_function_keys skips functions with zero count."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        body_start_line=2,
        end_line=3,
        body_col_offset=0,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func): {"total": 0, "by_test": {}},
        }
    }

    result = collect_executed_function_keys(
        source_file, tmp_path, [func], coverage_results
    )

    assert len(result) == 0


def test_collect_executed_function_keys_missing_file_in_coverage(
    tmp_path: Path,
) -> None:
    """collect_executed_function_keys handles missing file in coverage data."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        body_start_line=2,
        end_line=3,
        body_col_offset=0,
        return_type="int",
    )

    coverage_results: dict[str, int] = {}

    result = collect_executed_function_keys(
        source_file, tmp_path, [func], coverage_results
    )

    assert len(result) == 0


def test_collect_test_to_function_map_nested_format(tmp_path: Path) -> None:
    """collect_test_to_function_map extracts test-to-function mapping from nested format."""
    source_file = tmp_path / "mod.py"

    func1 = FunctionInfo(
        name="foo",
        class_name=None,
        file_path=source_file,
        line_number=1,
        body_start_line=2,
        end_line=3,
        body_col_offset=0,
        return_type="int",
    )

    func2 = FunctionInfo(
        name="bar",
        class_name=None,
        file_path=source_file,
        line_number=5,
        body_start_line=6,
        end_line=7,
        body_col_offset=0,
        return_type="str",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func1): {"total": 5, "by_test": {"test_x": 3, "test_y": 2}},
            function_key(func2): {"total": 2, "by_test": {"test_x": 2}},
        }
    }

    result = collect_test_to_function_map(source_file, tmp_path, coverage_results)

    assert "test_x" in result
    assert "test_y" in result
    assert function_key(func1) in result["test_x"]
    assert function_key(func2) in result["test_x"]
    assert function_key(func1) in result["test_y"]
    assert function_key(func2) not in result["test_y"]


def test_collect_test_to_function_map_legacy_format_ignored(tmp_path: Path) -> None:
    """collect_test_to_function_map ignores legacy flat format data."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="foo",
        class_name=None,
        file_path=source_file,
        line_number=1,
        body_start_line=2,
        end_line=3,
        body_col_offset=0,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {relative: {function_key(func): 10}}

    result = collect_test_to_function_map(source_file, tmp_path, coverage_results)
    assert len(result) == 0


def test_collect_test_to_function_map_no_data(tmp_path: Path) -> None:
    """collect_test_to_function_map handles missing coverage data gracefully."""
    source_file = tmp_path / "mod.py"
    result = collect_test_to_function_map(source_file, tmp_path, {})
    assert result == {}
