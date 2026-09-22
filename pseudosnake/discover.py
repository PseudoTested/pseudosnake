"""Functions for discovering Python source files and functions in a project."""

import ast
from dataclasses import dataclass
from pathlib import Path

# data structures


@dataclass
class FunctionInfo:
    """Metadata about a discovered Python function extracted from its AST.

    *line_number*     — the ``def`` line.
    *body_start_line* — first executable statement inside the function body.
    *end_line*        — last line of the function (closing scope).
    *body_col_offset* — indentation column of the function body.
    *return_type*     — normalised lowercase type name (``"unknown"`` when
                        no annotation is present).
    """

    name: str
    class_name: str | None
    file_path: Path
    line_number: int
    body_start_line: int
    end_line: int
    body_col_offset: int
    return_type: str


# file-level discovery

# directories that are always skipped during file discovery
_EXCLUDED_PARTS = frozenset(
    {
        ".venv",
        "venv",
        ".tox",
        "__pycache__",
        ".git",
        "node_modules",
        ".mypy_cache",
        ".ruff_cache",
        ".pytest_cache",
        "build",
        "dist",
    }
)


def _in_excluded_dir(path: Path) -> bool:
    """Return True when *path* is inside a venv, cache, or build directory."""
    # check if any path part matches an excluded directory name
    return bool(_EXCLUDED_PARTS.intersection(path.parts))


def is_test_file(path: Path, project_dir: Path | None = None) -> bool:
    """Return True if the path matches common test-file patterns."""
    name = path.name
    # match test_*.py or *_test.py by filename
    if name.startswith("test_") or name.endswith("_test.py"):
        return True
    # match anything inside a tests/ directory, relative to project_dir
    if project_dir is not None:
        try:
            relative = path.relative_to(project_dir)
        except ValueError:
            relative = path
    else:
        relative = path
    return "tests" in relative.parts


def find_test_files(project_dir: Path) -> list[Path]:
    """Return all test Python files under *project_dir*, excluding venv/caches."""
    test_files: list[Path] = []
    # walk the entire project directory tree for .py files
    for py_file in sorted(project_dir.rglob("*.py")):
        # skip files inside .venv, __pycache__, .git, etc.
        if _in_excluded_dir(py_file):
            continue
        # only collect files that look like tests
        if is_test_file(py_file, project_dir):
            test_files.append(py_file)
    return test_files


def find_all_python_files(project_dir: Path) -> list[Path]:
    """Return every ``.py`` file under *project_dir*, excluding venv/caches."""
    return [p for p in sorted(project_dir.rglob("*.py")) if not _in_excluded_dir(p)]


def find_python_files(
    project_dir: Path,
    single_file: Path | None = None,
    source_dir: Path | None = None,
) -> list[Path]:
    """Return the list of Python source files to analyse."""
    # single-file mode — use the provided path directly
    if single_file is not None:
        candidate = (
            single_file if single_file.is_absolute() else project_dir / single_file
        )
        return [candidate]

    # multi-file mode — determine the directory to scan
    search_root = _resolve_source_root(project_dir, source_dir)
    if not search_root.is_dir():
        raise FileNotFoundError(
            f"Source directory not found: {search_root} ({search_root.resolve()}). "
            "Check --project-dir and --source-dir."
        )
    # recursively find .py files, skipping test files and excluded directories
    return [
        p
        for p in sorted(search_root.rglob("*.py"))
        if not is_test_file(p, project_dir) and not _in_excluded_dir(p)
    ]


def _resolve_source_root(project_dir: Path, source_dir: Path | None) -> Path:
    """Pick the directory that should be scanned for source files."""
    # explicit override takes priority
    if source_dir is not None:
        return source_dir if source_dir.is_absolute() else project_dir / source_dir

    # common src-layout: project/src/
    src_root = project_dir / "src"
    if src_root.is_dir():
        return src_root

    # package-directory layout: project/<name>/ (has __init__.py)
    package_like = project_dir / project_dir.resolve().name
    if package_like.is_dir() and (package_like / "__init__.py").exists():
        return package_like

    # fallback: flat layout, scan from project root
    return project_dir


# function-level discovery (ast parsing)

# function names with these prefixes are PseudoSnake's own instrumentation
# and must never be treated as project functions during discovery
_INTERNAL_PREFIXES = ("__pseudosnake_", "__ps_")


def _is_internal_name(name: str) -> bool:
    """Return True when *name* belongs to PseudoSnake's own instrumentation."""
    return name.startswith(_INTERNAL_PREFIXES)


def find_functions(file_path: Path) -> list[FunctionInfo]:
    """Parse a Python source file and return all discovered functions.

    Silently returns an empty list on parse or I/O errors.
    """
    try:
        # read and parse the source file into an abstract syntax tree
        source = file_path.read_text(encoding="utf-8")
        tree = ast.parse(source, filename=str(file_path))
    except (SyntaxError, OSError):
        # bad syntax or unreadable file — return empty, don't crash
        return []

    # walk the tree and collect function definitions
    results: list[FunctionInfo] = []
    _collect_functions(tree, file_path, None, results)
    return results


def _collect_functions(
    node: ast.AST,
    file_path: Path,
    class_name: str | None,
    results: list[FunctionInfo],
) -> None:
    """Walk *node* children and collect top-level and class-level functions."""
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            # recurse into class bodies, passing the class name as context
            _collect_functions(child, file_path, child.name, results)
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # skip PseudoSnake's own instrumentation functions
            if _is_internal_name(child.name):
                continue
            # found a function — extract metadata and add to results
            results.append(_make_function_info(child, file_path, class_name))


def _make_function_info(
    node: ast.FunctionDef | ast.AsyncFunctionDef,
    file_path: Path,
    class_name: str | None,
) -> FunctionInfo:
    """Build a ``FunctionInfo`` from an AST function node."""
    # the first child of a function body is the first executable statement
    body_node = node.body[0]
    return FunctionInfo(
        name=node.name,
        class_name=class_name,
        file_path=file_path,
        line_number=node.lineno,  # line of the "def" statement
        body_start_line=body_node.lineno,  # line of first body statement
        end_line=node.end_lineno or body_node.lineno,
        body_col_offset=body_node.col_offset,  # indentation of body
        return_type=_extract_return_type(node),
    )


def _extract_return_type(
    func_node: ast.FunctionDef | ast.AsyncFunctionDef,
) -> str:
    """Extract a normalised return-type string from the function annotation.

    Returns a lowercase type name such as ``"int"``, ``"str"``, ``"list"``,
    ``"none"``, or ``"unknown"`` when the type cannot be determined.
    """
    # the return annotation node from the ast (the part after ->)
    annotation = func_node.returns

    # no annotation at all
    if annotation is None:
        return "unknown"
    # -> None
    if isinstance(annotation, ast.Constant) and annotation.value is None:
        return "none"
    # -> int, -> str, -> bool, etc.
    if isinstance(annotation, ast.Name):
        return annotation.id.lower()
    # -> list[int], -> dict[str, int], etc. (subscript/generic)
    if isinstance(annotation, ast.Subscript):
        inner = annotation.value
        # list[int] → "list"
        if isinstance(inner, ast.Name):
            return inner.id.lower()
        # typing.Optional[str] → "optional"
        if isinstance(inner, ast.Attribute):
            return inner.attr.lower()
    # -> module.Type (attribute access)
    if isinstance(annotation, ast.Attribute):
        return annotation.attr.lower()
    # union types (int | None) and everything else
    return "unknown"
