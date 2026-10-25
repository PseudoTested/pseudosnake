"""Tests for pseudosnake.mutate — mutant generation and application."""

import asyncio
import inspect
import textwrap


from pseudosnake.discover import FunctionInfo, find_functions
from pseudosnake.mutate import (
    MUTANTS_BY_TYPE,
    MutationApplicationError,
    apply_mutant,
    generate_mutants,
)


def test_generate_mutants_int() -> None:
    """generate_mutants returns the correct pair for int."""
    assert generate_mutants("int") == ["return 0", "return 1"]


def test_generate_mutants_bool() -> None:
    """generate_mutants returns False/True pair for bool."""
    assert generate_mutants("bool") == ["return False", "return True"]


def test_generate_mutants_none() -> None:
    """generate_mutants returns empty for none type — return None is a no-op."""
    assert generate_mutants("none") == []


def test_generate_mutants_unknown_falls_back() -> None:
    """generate_mutants falls back to return None for unrecognised types."""
    assert generate_mutants("something_exotic") == ["return None"]


def test_generate_mutants_all_types_covered() -> None:
    """Every key in MUTANTS_BY_TYPE should return a non-empty list
    (except 'none' which has no meaningful mutant)."""
    for type_name in MUTANTS_BY_TYPE:
        mutants = generate_mutants(type_name)
        if type_name == "none":
            assert mutants == []
        else:
            assert len(mutants) >= 1, f"No mutants for type '{type_name}'"


def _make_func_info(src: str, tmp_path: object) -> FunctionInfo:
    """Helper: write src to a temp file and return the first FunctionInfo."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    f = tmp_path / "mod.py"
    f.write_text(src)
    funcs = find_functions(f)
    assert funcs, "No functions found in test source"
    return funcs[0]


def test_apply_mutant_replaces_body(tmp_path: object) -> None:
    """apply_mutant replaces the full function body with the mutant statement."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            x = a + b
            return x
    """)
    func_info = _make_func_info(src, tmp_path)
    source_lines = src.splitlines(keepends=True)
    result = apply_mutant(source_lines, func_info, "return 0")
    joined = "".join(result)
    assert "return 0" in joined
    assert "x = a + b" not in joined
    assert "def add" in joined


def test_apply_mutant_preserves_lines_after_function(tmp_path: object) -> None:
    """apply_mutant does not alter lines that follow the function."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b

        CONSTANT = 42
    """)
    func_info = _make_func_info(src, tmp_path)
    source_lines = src.splitlines(keepends=True)
    result = apply_mutant(source_lines, func_info, "return 0")
    joined = "".join(result)
    assert "CONSTANT = 42" in joined


def test_apply_mutant_handles_decorated_multiline_function(tmp_path: object) -> None:
    """Decorators and multiline signatures remain valid and unchanged."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = textwrap.dedent(
        '''\
        def decorate(function):
            return function

        @decorate
        def target(
            first: int,
            second: int,
        ) -> int:
            """Original documentation."""
            return first + second

        def after() -> int:
            return 42
        '''
    )
    target = tmp_path / "mod.py"
    target.write_text(src, encoding="utf-8")
    func_info = next(
        function for function in find_functions(target) if function.name == "target"
    )

    mutated = "".join(
        apply_mutant(src.splitlines(keepends=True), func_info, "return 0")
    )

    compile(mutated, str(target), "exec")
    assert "@decorate\ndef target(\n" in mutated
    assert "Original documentation" not in mutated
    assert "def after()" in mutated


def test_apply_mutant_expands_one_line_suite(tmp_path: object) -> None:
    """A one-line function becomes a valid indented mutant body."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = "def answer() -> int: return 42\n"
    func_info = _make_func_info(src, tmp_path)

    mutated = "".join(
        apply_mutant(src.splitlines(keepends=True), func_info, "return 0")
    )

    compile(mutated, str(func_info.file_path), "exec")
    assert mutated == "def answer() -> int: return 0\n"


def test_apply_mutant_replaces_async_function_body(tmp_path: object) -> None:
    """Async syntax is preserved while the selected body is replaced."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = textwrap.dedent("""\
        async def answer() -> int:
            value = 42
            return value
    """)
    func_info = _make_func_info(src, tmp_path)

    mutated = "".join(
        apply_mutant(src.splitlines(keepends=True), func_info, "return 0")
    )
    namespace: dict[str, object] = {}
    exec(compile(mutated, str(func_info.file_path), "exec"), namespace)
    answer = namespace["answer"]

    assert "async def answer" in mutated
    assert "value = 42" not in mutated
    assert inspect.iscoroutinefunction(answer)
    assert asyncio.run(answer()) == 0


def test_apply_mutant_targets_concrete_overload_implementation(
    tmp_path: object,
) -> None:
    """Overload stubs are preserved while the implementation is replaced."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = textwrap.dedent(
        """\
        from typing import overload

        @overload
        def parse(value: int) -> str: ...

        def parse(value: object) -> str:
            return str(value)
        """
    )
    target = tmp_path / "mod.py"
    target.write_text(src, encoding="utf-8")
    functions = find_functions(target)
    assert len(functions) == 1

    mutated = "".join(
        apply_mutant(src.splitlines(keepends=True), functions[0], 'return ""')
    )

    compile(mutated, str(target), "exec")
    assert "def parse(value: int) -> str: ..." in mutated
    assert "return str(value)" not in mutated


def test_apply_mutant_rejects_stale_function_position(tmp_path: object) -> None:
    """A stale function identity cannot mutate a similarly named function."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    src = "def answer() -> int:\n    return 42\n"
    func_info = _make_func_info(src, tmp_path)
    func_info.line_number = 99

    try:
        apply_mutant(src.splitlines(keepends=True), func_info, "return 0")
    except MutationApplicationError as exc:
        assert "found 0" in str(exc)
    else:
        raise AssertionError("stale function position should be rejected")
