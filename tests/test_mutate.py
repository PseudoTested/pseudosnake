"""Tests for pseudosnake.mutate — mutant generation and application."""

import textwrap


from pseudosnake.discover import FunctionInfo, find_functions
from pseudosnake.mutate import MUTANTS_BY_TYPE, apply_mutant, generate_mutants


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
