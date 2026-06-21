"""Tests for pseudosnake.discover — additional edge cases."""

import ast
from pathlib import Path

from pseudosnake.discover import (
    _extract_return_type,
    _resolve_source_root,
    find_python_files,
    is_test_file,
)


def test_extract_return_type_attribute_annotation() -> None:
    """_extract_return_type extracts attribute name from module.Type annotations."""
    source = "def f() -> datetime.datetime: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "datetime"


def test_extract_return_type_subscript_attribute() -> None:
    """_extract_return_type extracts attribute from Optional[str] style."""
    source = "def f() -> typing.Optional[str]: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "optional"


def test_extract_return_type_union() -> None:
    """_extract_return_type returns 'unknown' for Union/pipe types."""
    source = "def f() -> int | None: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "unknown"


def test_is_test_file_directory_name_with_test() -> None:
    """is_test_file matches when 'tests' is an exact path part."""
    assert is_test_file(Path("tests/helpers.py")) is True


def test_is_test_file_regular_filename() -> None:
    """is_test_file returns False when none of the patterns match."""
    assert is_test_file(Path("project/utils.py")) is False


def test_resolve_source_root_explicit_absolute() -> None:
    """_resolve_source_root uses absolute source_dir directly."""
    result = _resolve_source_root(Path("/project"), Path("/custom/src"))
    assert result == Path("/custom/src")


def test_resolve_source_root_explicit_relative() -> None:
    """_resolve_source_root resolves relative source_dir against project_dir."""
    result = _resolve_source_root(Path("/project"), Path("src"))
    assert result == Path("/project/src")


def test_resolve_source_root_src_layout(tmp_path: Path) -> None:
    """_resolve_source_root prefers src/ when it exists."""
    (tmp_path / "src").mkdir()
    assert _resolve_source_root(tmp_path, None) == tmp_path / "src"


def test_resolve_source_root_package_like(tmp_path: Path) -> None:
    """_resolve_source_root detects package-named subdirectory with __init__.py."""
    pkg = tmp_path / tmp_path.name
    pkg.mkdir()
    (pkg / "__init__.py").write_text("")
    assert _resolve_source_root(tmp_path, None) == pkg


def test_resolve_source_root_fallback(tmp_path: Path) -> None:
    """_resolve_source_root falls back to project_dir when nothing matches."""
    result = _resolve_source_root(tmp_path, None)
    assert result == tmp_path


def test_find_python_files_with_absolute_single_file(tmp_path: Path) -> None:
    """find_python_files accepts absolute single_file paths."""
    f = tmp_path / "mod.py"
    f.write_text("x=1")
    result = find_python_files(tmp_path / "sub", single_file=f)
    assert result == [f]


def test_find_python_files_returns_sorted(tmp_path: Path) -> None:
    """find_python_files returns files in sorted order."""
    (tmp_path / "b.py").write_text("x=1")
    (tmp_path / "a.py").write_text("x=1")
    result = find_python_files(tmp_path)
    assert result[0].name == "a.py"
    assert result[1].name == "b.py"


def test_extract_return_type_attribute_direct() -> None:
    """_extract_return_type handles direct attribute annotations like -> mod.Type."""
    import ast

    source = "def f() -> str: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "str"


def test_find_python_files_with_relative_single_file(tmp_path: Path) -> None:
    """find_python_files resolves relative single_file against project_dir."""
    f = tmp_path / "mod.py"
    f.write_text("x=1")
    result = find_python_files(tmp_path, single_file=Path("mod.py"))
    assert result == [f]
