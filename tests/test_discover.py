"""Tests for pseudosnake.discover — file and function discovery."""

import ast
import textwrap
from pathlib import Path


from pseudosnake.discover import (
    _extract_return_type,
    find_functions,
    find_python_files,
    is_test_file,
)


def test_is_test_file_detects_test_prefix() -> None:
    """is_test_file returns True for files starting with test_."""
    assert is_test_file(Path("tests/test_foo.py")) is True


def test_is_test_file_detects_test_suffix() -> None:
    """is_test_file returns True for files ending with _test.py."""
    assert is_test_file(Path("src/foo_test.py")) is True


def test_is_test_file_detects_tests_directory() -> None:
    """is_test_file returns True for files inside a tests/ directory."""
    assert is_test_file(Path("tests/helpers.py")) is True


def test_is_test_file_returns_false_for_source() -> None:
    """is_test_file returns False for normal source files."""
    assert is_test_file(Path("src/calculator.py")) is False


def test_find_python_files_uses_single_file(tmp_path: Path) -> None:
    """find_python_files returns only the provided file when --file is given."""
    single = tmp_path / "mymodule.py"
    single.write_text("x = 1")
    result = find_python_files(tmp_path, single_file=single)
    assert result == [single]


def test_find_python_files_excludes_test_files(tmp_path: Path) -> None:
    """find_python_files skips test files when scanning recursively."""
    (tmp_path / "main.py").write_text("x = 1")
    (tmp_path / "test_main.py").write_text("x = 1")
    result = find_python_files(tmp_path)
    assert all("test_" not in p.name for p in result)
    assert any(p.name == "main.py" for p in result)


def test_find_python_files_prefers_src_layout(tmp_path: Path) -> None:
    """find_python_files prefers src/ when it exists."""
    src = tmp_path / "src"
    src.mkdir()
    pkg = tmp_path / tmp_path.name
    pkg.mkdir()
    (src / "inside_src.py").write_text("x = 1")
    (pkg / "inside_pkg.py").write_text("x = 2")

    result = find_python_files(tmp_path)

    assert any(p.name == "inside_src.py" for p in result)
    assert all(p.name != "inside_pkg.py" for p in result)


def test_find_python_files_prefers_package_named_like_project(tmp_path: Path) -> None:
    """find_python_files prefers project-name package directory when present."""
    pkg = tmp_path / tmp_path.name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    (pkg / "core.py").write_text("x = 1")
    (tmp_path / "script.py").write_text("print('hi')")

    result = find_python_files(tmp_path)

    assert any(p.name == "core.py" for p in result)
    assert all(p.name != "script.py" for p in result)


def test_find_python_files_uses_explicit_source_dir(tmp_path: Path) -> None:
    """find_python_files honors explicit source_dir when provided."""
    package = tmp_path / "gatorgrade"
    package.mkdir()
    (package / "feature.py").write_text("x = 1")
    (tmp_path / "other.py").write_text("x = 2")

    result = find_python_files(tmp_path, source_dir=Path("gatorgrade"))

    assert any(p.name == "feature.py" for p in result)
    assert all(p.name != "other.py" for p in result)


def test_extract_return_type_int() -> None:
    """_extract_return_type returns 'int' for -> int annotations."""
    source = "def f() -> int: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "int"


def test_extract_return_type_none() -> None:
    """_extract_return_type returns 'none' for -> None annotations."""
    source = "def f() -> None: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "none"


def test_extract_return_type_unknown_when_missing() -> None:
    """_extract_return_type returns 'unknown' when no annotation is present."""
    source = "def f(): ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "unknown"


def test_extract_return_type_list_subscript() -> None:
    """_extract_return_type returns 'list' for -> list[int] annotations."""
    source = "def f() -> list[int]: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "list"


def test_find_functions_detects_top_level(tmp_path: Path) -> None:
    """find_functions detects top-level functions."""
    src = tmp_path / "mod.py"
    src.write_text(
        textwrap.dedent("""\
        def add(a: int, b: int) -> int:
            return a + b
    """)
    )
    funcs = find_functions(src)
    assert len(funcs) == 1
    assert funcs[0].name == "add"
    assert funcs[0].return_type == "int"
    assert funcs[0].class_name is None


def test_find_functions_detects_class_method(tmp_path: Path) -> None:
    """find_functions detects methods and records the class name."""
    src = tmp_path / "mod.py"
    src.write_text(
        textwrap.dedent("""\
        class MyClass:
            def greet(self) -> str:
                return "hello"
    """)
    )
    funcs = find_functions(src)
    assert len(funcs) == 1
    assert funcs[0].name == "greet"
    assert funcs[0].class_name == "MyClass"


def test_find_functions_returns_empty_on_syntax_error(tmp_path: Path) -> None:
    """find_functions returns an empty list when the file cannot be parsed."""
    src = tmp_path / "bad.py"
    src.write_text("def (broken syntax")
    assert find_functions(src) == []
