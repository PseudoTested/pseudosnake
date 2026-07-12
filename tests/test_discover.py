"""Tests for pseudosnake.discover — file and function discovery."""

import ast
import textwrap
from pathlib import Path


from pseudosnake.discover import (
    _extract_return_type,
    _resolve_source_root,
    find_all_python_files,
    find_functions,
    find_python_files,
    find_test_files,
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


def test_is_test_file_regular_filename() -> None:
    """is_test_file returns False when none of the patterns match."""
    assert is_test_file(Path("project/utils.py")) is False


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


def test_find_python_files_with_relative_single_file(tmp_path: Path) -> None:
    """find_python_files resolves relative single_file against project_dir."""
    f = tmp_path / "mod.py"
    f.write_text("x=1")
    result = find_python_files(tmp_path, single_file=Path("mod.py"))
    assert result == [f]


def test_find_python_files_with_file_argument_returns_single_file(
    tmp_path: Path,
) -> None:
    """find_python_files returns only the specified file when single_file argument is used."""
    src_dir = tmp_path / "src"
    src_dir.mkdir()

    file1 = src_dir / "module1.py"
    file2 = src_dir / "module2.py"
    file1.write_text("def foo(): pass")
    file2.write_text("def bar(): pass")

    result = find_python_files(tmp_path, single_file=file1)

    assert len(result) == 1
    assert file1 in result
    assert file2 not in result


def test_find_python_files_with_source_dir_argument(tmp_path: Path) -> None:
    """find_python_files restricts discovery to specified source directory."""
    src_dir = tmp_path / "src"
    other_dir = tmp_path / "other"
    src_dir.mkdir()
    other_dir.mkdir()

    src_file = src_dir / "module.py"
    other_file = other_dir / "module.py"
    src_file.write_text("def foo(): pass")
    other_file.write_text("def bar(): pass")

    result = find_python_files(tmp_path, source_dir=src_dir)

    assert src_file in result
    assert other_file not in result


def test_find_test_files_returns_test_files_only(tmp_path: Path) -> None:
    """find_test_files only returns files matching test patterns."""
    test_file1 = tmp_path / "test_module.py"
    test_file2 = tmp_path / "module_test.py"
    regular_file = tmp_path / "module.py"

    test_file1.write_text("def test_foo(): pass")
    test_file2.write_text("def test_bar(): pass")
    regular_file.write_text("def regular(): pass")

    result = find_test_files(tmp_path)

    assert test_file1 in result
    assert test_file2 in result
    assert regular_file not in result


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


def test_extract_return_type_attribute_direct() -> None:
    """_extract_return_type handles direct attribute annotations like -> mod.Type."""
    source = "def f() -> str: ..."
    tree = ast.parse(source)
    node = tree.body[0]
    assert isinstance(node, ast.FunctionDef)
    assert _extract_return_type(node) == "str"


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


def test_find_functions_with_class_methods(tmp_path: Path) -> None:
    """find_functions discovers methods inside classes."""
    source_file = tmp_path / "module.py"
    source_file.write_text(
        """
class MyClass:
    def method1(self) -> int:
        return 1

    def method2(self) -> str:
        return "hello"

def standalone() -> None:
    pass
"""
    )
    functions = find_functions(source_file)

    names = [f.name for f in functions]
    assert "method1" in names
    assert "method2" in names
    assert "standalone" in names

    for func in functions:
        if func.name in ["method1", "method2"]:
            assert func.class_name == "MyClass"
        else:
            assert func.class_name is None


def test_find_functions_with_nested_classes(tmp_path: Path) -> None:
    """find_functions discovers methods in nested classes."""
    source_file = tmp_path / "module.py"
    source_file.write_text(
        """
class Outer:
    class Inner:
        def inner_method(self) -> str:
            return "nested"

    def outer_method(self) -> int:
        return 1
"""
    )
    functions = find_functions(source_file)

    names = [f.name for f in functions]
    assert "outer_method" in names


def test_find_functions_with_multiple_decorators(tmp_path: Path) -> None:
    """find_functions handles functions with multiple decorators."""
    source_file = tmp_path / "module.py"
    source_file.write_text(
        """
def decorator1(func):
    return func

def decorator2(func):
    return func

@decorator1
@decorator2
def decorated_func() -> None:
    pass
"""
    )
    functions = find_functions(source_file)

    names = [f.name for f in functions]
    assert "decorated_func" in names


def test_find_all_python_files_excludes_venv(tmp_path: Path) -> None:
    """find_all_python_files returns .py files excluding venv/caches."""
    (tmp_path / "mod.py").write_text("x")
    (tmp_path / "tests").mkdir()
    (tmp_path / "tests" / "test_mod.py").write_text("x")
    (tmp_path / "not_py.txt").write_text("x")
    venv = tmp_path / ".venv"
    venv.mkdir()
    (venv / "pkg.py").write_text("x")

    result = find_all_python_files(tmp_path)
    paths = {str(p.relative_to(tmp_path)) for p in result}
    assert paths == {"mod.py", "tests/test_mod.py"}
