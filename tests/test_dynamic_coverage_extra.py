"""Tests for pseudosnake.dynamic_coverage — additional edge cases."""

import json
from pathlib import Path

from pseudosnake.discover import FunctionInfo
from pseudosnake.dynamic_coverage import (
    _build_header_block,
    _find_module_insert_index,
    collect_executed_function_keys,
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


def test_find_module_insert_index_no_special_lines() -> None:
    """_find_module_insert_index starts at 0 when no special lines present."""
    lines = ["x = 1\n"]
    idx = _find_module_insert_index(lines)
    assert idx == 0


def test_find_module_insert_index_empty() -> None:
    """_find_module_insert_index handles empty source."""
    idx = _find_module_insert_index([])
    assert idx == 0


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


def test_collect_executed_function_keys_multiple_functions(tmp_path: Path) -> None:
    """collect_executed_function_keys returns only executed keys across functions."""
    source = "def a():\n    pass\n\ndef b():\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    from pseudosnake.discover import find_functions

    functions = find_functions(target)
    coverage = {"mod.py": {"a": 1, "b": 0}}
    executed = collect_executed_function_keys(target, tmp_path, functions, coverage)
    assert executed == ["a"]


def test_collect_executed_function_keys_no_executed(tmp_path: Path) -> None:
    """collect_executed_function_keys returns empty when no function executed."""
    source = "def a():\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    from pseudosnake.discover import find_functions

    functions = find_functions(target)
    coverage: dict[str, dict[str, int]] = {}
    executed = collect_executed_function_keys(target, tmp_path, functions, coverage)
    assert executed == []


def test_instrument_file_source_empty_functions() -> None:
    """instrument_file_source returns source unchanged when no functions."""
    source = "x = 1\ny = 2\n"
    result = instrument_file_source(source, [], "mod.py")
    assert result == source


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


def test_load_dynamic_coverage_results_not_a_dict(tmp_path: Path) -> None:
    """load_dynamic_coverage_results returns empty when JSON is not a dict."""
    path = tmp_path / "cov.json"
    path.write_text("[1, 2, 3]")
    result = load_dynamic_coverage_results(path)
    assert result == {}
