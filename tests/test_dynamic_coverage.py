"""Tests for experimental dynamic coverage instrumentation helpers."""

import asyncio
import atexit
import builtins
import json
from pathlib import Path
from typing import Any

from pseudosnake.discover import FunctionInfo, find_functions
from pseudosnake.dynamic_coverage import (
    FunctionCoverage,
    _build_header_block,
    _find_module_insert_index,
    build_coverage_bootstrap,
    collect_function_coverage,
    function_key,
    instrument_file_source,
    instrument_test_file,
    load_dynamic_coverage_results,
)


def test_function_key_top_level() -> None:
    """function_key includes the definition line for top-level functions."""
    func = FunctionInfo(
        name="my_func",
        class_name=None,
        file_path=Path("mod.py"),
        line_number=1,
        return_type="int",
    )
    assert function_key(func) == "my_func@1"


def test_function_key_method() -> None:
    """function_key includes class name and definition line for methods."""
    func = FunctionInfo(
        name="my_method",
        class_name="MyClass",
        file_path=Path("mod.py"),
        line_number=10,
        return_type="str",
    )
    assert function_key(func) == "MyClass.my_method@10"


def test_build_header_block_structure() -> None:
    """_build_header_block returns a string with the expected keywords."""
    header = _build_header_block("test.py")
    assert "__pseudosnake_cov__" in header
    assert "__pseudosnake_register__" in header
    assert "test.py" in header
    assert "import " not in header


def test_build_coverage_bootstrap_structure() -> None:
    """The process bootstrap owns counters, flushing, and required imports."""
    bootstrap = build_coverage_bootstrap()
    assert "__pseudosnake_sources__" in bootstrap
    assert "__pseudosnake_register__" in bootstrap
    assert "__pseudosnake_flush_cov__" in bootstrap
    assert "PSEUDOSNAKE_DYN_COV_PATH" in bootstrap
    assert "atexit" in bootstrap


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
    assert "__pseudosnake_test_context__" in instrumented
    assert "add@1" in instrumented


def test_instrument_file_source_handles_real_python_shapes(
    tmp_path: Path, monkeypatch
) -> None:
    """Instrumentation preserves syntax, future imports, and docstrings."""
    source = '''"""Module documentation."""

from __future__ import annotations
import typing as t

def decorator(func):
    return func

def documented(value: int) -> int:
    """Keep this function documentation."""
    @decorator
    def nested():
        return value
    return nested()

def one_line(): return 1

@t.overload
def overloaded(
    value: int,
) -> int: ...

def overloaded(value: int) -> int:
    return value
'''
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    registrations: dict[str, dict[str, int]] = {}

    def register(file_name: str, counters: dict[str, int]) -> None:
        registrations[file_name] = counters

    monkeypatch.setattr(builtins, "_pseudosnake_register__", register, raising=False)
    instrumented = instrument_file_source(source, find_functions(target), "mod.py")
    compiled = compile(instrumented, str(target), "exec")
    # exec() populates the namespace with runtime objects whose precise types
    # are intentionally determined by the generated source under test.
    namespace: dict[str, Any] = {}
    exec(compiled, namespace)

    documented = namespace["documented"]
    one_line = namespace["one_line"]
    overloaded = namespace["overloaded"]
    assert callable(documented)
    assert callable(one_line)
    assert documented.__doc__ == "Keep this function documentation."
    assert documented(3) == 3
    assert one_line() == 1
    assert overloaded(4) == 4
    assert registrations["mod.py"]["documented@9"] == 1
    assert registrations["mod.py"]["one_line@16"] == 1
    assert "overloaded@19" not in registrations["mod.py"]
    assert registrations["mod.py"]["overloaded@23"] == 1


def test_instrument_file_source_handles_class_methods(
    tmp_path: Path, monkeypatch
) -> None:
    """Class methods are instrumented without touching nested functions."""
    source = """class Example:
    def method(self):
        def nested():
            return 1
        return nested()
"""
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    registrations: dict[str, dict[str, int]] = {}

    def register(file_name: str, counters: dict[str, int]) -> None:
        registrations[file_name] = counters

    monkeypatch.setattr(builtins, "_pseudosnake_register__", register, raising=False)
    instrumented = instrument_file_source(source, find_functions(target), "mod.py")
    # The generated class is only known after this source is executed.
    namespace: dict[str, Any] = {}
    exec(compile(instrumented, str(target), "exec"), namespace)

    example_class = namespace["Example"]
    assert example_class().method() == 1
    assert registrations["mod.py"]["Example.method@2"] == 1


def test_instrument_file_source_handles_async_functions(
    tmp_path: Path, monkeypatch
) -> None:
    """Async functions and methods receive counters and remain awaitable."""
    source = """async def top_level() -> int:
    return 7

class Worker:
    async def method(self) -> int:
        return 9
"""
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    registrations: dict[str, dict[str, int]] = {}

    def register(file_name: str, counters: dict[str, int]) -> None:
        registrations[file_name] = counters

    monkeypatch.setattr(builtins, "_pseudosnake_register__", register, raising=False)
    instrumented = instrument_file_source(source, find_functions(target), "mod.py")
    # The generated coroutine functions are runtime values produced by exec().
    namespace: dict[str, Any] = {}
    exec(compile(instrumented, str(target), "exec"), namespace)

    top_level = namespace["top_level"]
    worker_class = namespace["Worker"]
    assert asyncio.run(top_level()) == 7
    assert asyncio.run(worker_class().method()) == 9
    assert registrations["mod.py"]["top_level@1"] == 1
    assert registrations["mod.py"]["Worker.method@5"] == 1


def test_instrument_file_source_empty_functions() -> None:
    """instrument_file_source returns source unchanged when no functions."""
    source = "x = 1\ny = 2\n"
    result = instrument_file_source(source, [], "mod.py")
    assert result == source


def test_instrument_test_file_marks_only_tests(tmp_path: Path) -> None:
    """Test instrumentation preserves helpers and marks sync and async tests."""
    source = '''def helper():
    return 1

async def test_async():
    """Keep the test docstring."""
    return helper()

class TestExample:
    def test_method(self):
        return helper()
'''
    target = tmp_path / "test_mod.py"
    target.write_text(source, encoding="utf-8")

    instrumented = instrument_test_file(
        source, find_functions(target), "tests/test_mod.py"
    )

    compile(instrumented, str(target), "exec")
    assert instrumented.count("__pseudosnake_test_context__[0] =") == 2
    assert "tests/test_mod.py::test_async" in instrumented
    assert "tests/test_mod.py::TestExample::test_method" in instrumented
    assert '"""Keep the test docstring."""' in instrumented


def test_instrumentation_records_per_test_hits(tmp_path: Path, monkeypatch) -> None:
    """Source hits are grouped under the test marker that invoked them."""
    coverage_path = tmp_path / "coverage.json"
    monkeypatch.setenv("PSEUDOSNAKE_DYN_COV_PATH", str(coverage_path))
    # Prevent this test's bootstrap callback from running again at pytest exit.
    monkeypatch.setattr(atexit, "register", lambda callback: callback)
    # Register these attributes through monkeypatch first so their original
    # process values are restored after the generated bootstrap overwrites them.
    monkeypatch.setattr(builtins, "_pseudosnake_register__", None, raising=False)
    monkeypatch.setattr(builtins, "_pseudosnake_test_context__", None, raising=False)

    bootstrap_namespace: dict[str, Any] = {}
    exec(build_coverage_bootstrap(), bootstrap_namespace)

    source = "def target():\n    return 1\n"
    source_path = tmp_path / "mod.py"
    source_path.write_text(source, encoding="utf-8")
    source_namespace: dict[str, Any] = {}
    exec(
        compile(
            instrument_file_source(source, find_functions(source_path), "mod.py"),
            str(source_path),
            "exec",
        ),
        source_namespace,
    )

    test_source = "def test_target(callback):\n    callback()\n"
    test_path = tmp_path / "test_mod.py"
    test_path.write_text(test_source, encoding="utf-8")
    test_namespace: dict[str, Any] = {}
    exec(
        compile(
            instrument_test_file(
                test_source, find_functions(test_path), "tests/test_mod.py"
            ),
            str(test_path),
            "exec",
        ),
        test_namespace,
    )

    test_namespace["test_target"](source_namespace["target"])
    bootstrap_namespace["__pseudosnake_flush_cov__"]()

    assert json.loads(coverage_path.read_text(encoding="utf-8")) == {
        "mod.py": {
            "target@1": {
                "total": 1,
                "by_test": {"tests/test_mod.py::test_target": 1},
            }
        }
    }


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


def test_collect_function_coverage_normalizes_positive_and_zero_counts(
    tmp_path: Path,
) -> None:
    """Coverage records retain both executed and unexecuted functions."""
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

    result = collect_function_coverage(target, tmp_path, functions, coverage)
    assert result == {
        "a@1": FunctionCoverage(total_calls=3),
        "b@5": FunctionCoverage(),
    }


def test_collect_function_coverage_multiple_functions(tmp_path: Path) -> None:
    """collect_function_coverage returns one record for every function."""
    source = "def a():\n    pass\n\ndef b():\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    functions = find_functions(target)
    coverage = {"mod.py": {"a": 1, "b": 0}}
    result = collect_function_coverage(target, tmp_path, functions, coverage)
    assert result["a@1"].covered is True
    assert result["b@4"].covered is False


def test_collect_function_coverage_no_executed(tmp_path: Path) -> None:
    """collect_function_coverage returns a zero record when nothing executed."""
    source = "def a():\n    pass\n"
    target = tmp_path / "mod.py"
    target.write_text(source, encoding="utf-8")

    functions = find_functions(target)
    coverage: dict[str, dict[str, int]] = {}
    result = collect_function_coverage(target, tmp_path, functions, coverage)
    assert result == {"a@1": FunctionCoverage()}


def test_collect_function_coverage_with_nested_format(tmp_path: Path) -> None:
    """Nested coverage retains caller-aware test attribution."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func): {"total": 5, "by_test": {"test_x": 3, "test_y": 2}},
        }
    }

    result = collect_function_coverage(source_file, tmp_path, [func], coverage_results)

    record = result[function_key(func)]
    assert record == FunctionCoverage(total_calls=5, by_test={"test_x": 3, "test_y": 2})
    assert record.to_report_dict() == {
        "total_calls": 5,
        "attributed_calls": 5,
        "unattributed_calls": 0,
        "by_test": {"test_x": 3, "test_y": 2},
    }


def test_collect_function_coverage_with_legacy_format(tmp_path: Path) -> None:
    """Legacy flat counts remain usable but are reported as unattributed."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func): 10,
        }
    }

    result = collect_function_coverage(source_file, tmp_path, [func], coverage_results)

    record = result[function_key(func)]
    assert record.to_report_dict() == {
        "total_calls": 10,
        "attributed_calls": 0,
        "unattributed_calls": 10,
        "by_test": {},
    }


def test_collect_function_coverage_zero_count_not_covered(tmp_path: Path) -> None:
    """A zero-count coverage record is retained and marked uncovered."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        return_type="int",
    )

    relative = str(source_file.relative_to(tmp_path))
    coverage_results = {
        relative: {
            function_key(func): {"total": 0, "by_test": {}},
        }
    }

    result = collect_function_coverage(source_file, tmp_path, [func], coverage_results)

    assert result[function_key(func)].covered is False


def test_collect_function_coverage_missing_file_in_coverage(
    tmp_path: Path,
) -> None:
    """A missing file produces zero records for its discovered functions."""
    source_file = tmp_path / "mod.py"

    func = FunctionInfo(
        name="test_func",
        class_name=None,
        file_path=source_file,
        line_number=1,
        return_type="int",
    )

    coverage_results: dict[str, int] = {}

    result = collect_function_coverage(source_file, tmp_path, [func], coverage_results)

    assert result == {function_key(func): FunctionCoverage()}


def test_collect_function_coverage_keeps_unattributed_calls(tmp_path: Path) -> None:
    """Calls not linked to a test remain explicit in the report record."""
    source_file = tmp_path / "mod.py"
    func = FunctionInfo(
        name="work",
        class_name=None,
        file_path=source_file,
        line_number=1,
        return_type="int",
    )
    coverage_results = {
        "mod.py": {
            function_key(func): {
                "total": 5,
                "by_test": {"tests/test_mod.py::test_work": 3},
            }
        }
    }

    record = collect_function_coverage(source_file, tmp_path, [func], coverage_results)[
        function_key(func)
    ]

    assert record.to_report_dict()["unattributed_calls"] == 2
