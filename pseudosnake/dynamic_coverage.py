"""Dynamic function-coverage instrumentation.

The standard AST identifies source and test function bodies, then targeted
text edits add lightweight counters and test markers. A process-wide bootstrap
shares the active test name with source modules and writes both total hits and
per-test hits at normal interpreter shutdown.
"""

from __future__ import annotations

import ast
import json
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from pseudosnake.discover import FunctionInfo
from pseudosnake.source_edit import (
    apply_text_edits,
    locate_function_nodes,
    prepend_function_statement,
)


# marker string injected into every instrumented file — used as an
# idempotency guard so re-instrumenting an already-instrumented file
# is a no-op
_INSTRUMENTATION_MARKER = "# PseudoSnake experimental dynamic coverage instrumentation"


@dataclass
class FunctionCoverage:
    """Normalized call counts for one discovered source function."""

    total_calls: int = 0
    by_test: dict[str, int] = field(default_factory=dict)

    @property
    def covered(self) -> bool:
        """Return whether the function ran at least once."""
        return self.total_calls > 0

    def to_report_dict(self) -> dict[str, Any]:
        """Return the stable JSON shape exposed in the final report."""
        attributed_calls = sum(self.by_test.values())
        return {
            "total_calls": self.total_calls,
            "attributed_calls": attributed_calls,
            # Calls made outside a marked test remain visible instead of being
            # incorrectly assigned to whichever test happened to run nearby.
            "unattributed_calls": max(self.total_calls - attributed_calls, 0),
            "by_test": dict(sorted(self.by_test.items())),
        }


# One normalized coverage record per stable function key, grouped by file.
CoverageMap = dict[str, dict[str, FunctionCoverage]]


def is_already_instrumented(source: str) -> bool:
    """Return True if *source* already contains PseudoSnake instrumentation."""
    return _INSTRUMENTATION_MARKER in source


# stable function identity


def function_key(func_info: FunctionInfo) -> str:
    """Return a stable function identifier for coverage records.

    The original definition line distinguishes overloads and repeated names.
    Instrumentation records keys before adding its module-level bootstrap, so
    this position remains stable for the duration of a coverage run.
    """
    # top-level functions are identified by just their name
    if func_info.class_name is None:
        qualified_name = func_info.name
    else:
        # class qualification prevents a method from colliding with a
        # top-level function that has the same name.
        qualified_name = f"{func_info.class_name}.{func_info.name}"
    # the original def line separates repeated definitions in the same scope.
    return f"{qualified_name}@{func_info.line_number}"


def instrument_file_source(
    source: str,
    functions: list[FunctionInfo],
    file_label: str,
) -> str:
    """Return *source* with syntax-aware runtime counters injected."""
    # do not edit files with no targets or files left instrumented by a
    # previous interrupted run.
    if not functions or _INSTRUMENTATION_MARKER in source:
        return source

    # resolve every FunctionInfo to its exact AST node before changing source
    # offsets. Each returned TextEdit still points into the original source.
    nodes = locate_function_nodes(source, functions)
    edits = []
    for function, node in zip(functions, nodes, strict=True):
        key = function_key(function)
        # Key each hit by the active test and source function. The bootstrap
        # later turns these tuple keys into the nested ``by_test`` JSON shape.
        counter_key = (
            f"((__pseudosnake_test_context__[0], {key!r}) "
            f"if __pseudosnake_test_context__[0] is not None else {key!r})"
        )
        # Keep the injected statement import-free and on one line because it
        # runs on every call and may be inserted into a one-line function.
        counter = (
            f"__pseudosnake_cov__[{counter_key}] = "
            f"__pseudosnake_cov__.get({counter_key}, 0) + 1"
        )
        edits.append(prepend_function_statement(source, node, counter))

    # apply all body edits together so one insertion cannot shift another
    # function's AST-derived offsets.
    instrumented = apply_text_edits(source, edits)
    lines = instrumented.splitlines(keepends=True)
    # Module setup belongs after the docstring and all __future__ imports.
    lines.insert(_find_module_insert_index(lines), _build_header_block(file_label))
    return "".join(lines)


def instrument_test_file(
    source: str,
    functions: list[FunctionInfo],
    file_label: str,
) -> str:
    """Return *source* with an active-test marker in each test function.

    Only functions whose names start with ``test`` are marked. Helpers and
    fixtures in test modules must not replace the identity of the test that
    called them.
    """
    test_functions = [
        function for function in functions if function.name.startswith("test")
    ]
    if not test_functions or _INSTRUMENTATION_MARKER in source:
        return source

    # Reuse the same AST resolver and targeted edit helper as source
    # instrumentation so decorators, async tests, and formatting are kept.
    nodes = locate_function_nodes(source, test_functions)
    edits = []
    for function, node in zip(test_functions, nodes, strict=True):
        qualified_name = function.name
        if function.class_name is not None:
            qualified_name = f"{function.class_name}::{function.name}"
        test_key = f"{file_label}::{qualified_name}"
        marker = f"__pseudosnake_test_context__[0] = {test_key!r}"
        edits.append(prepend_function_statement(source, node, marker))

    instrumented = apply_text_edits(source, edits)
    lines = instrumented.splitlines(keepends=True)
    lines.insert(_find_module_insert_index(lines), _build_test_header_block())
    return "".join(lines)


# header block (injected into every instrumented file)


def _build_header_block(file_label: str) -> str:
    """Build an import-free source counter registered with the bootstrap."""
    return (
        "\n# PseudoSnake experimental dynamic coverage instrumentation\n"
        "# Store this module's function hit counts locally.\n"
        "__pseudosnake_cov__ = {}\n"
        "# Use a local fallback only if the process bootstrap is unavailable.\n"
        "__pseudosnake_test_context__ = [None]\n"
        "try:\n"
        "    # sitecustomize exposes process-wide state via builtins.\n"
        "    if isinstance(__builtins__, dict):\n"
        "        __pseudosnake_register__ = __builtins__['_pseudosnake_register__']\n"
        "        __pseudosnake_test_context__ = __builtins__.get(\n"
        "            '_pseudosnake_test_context__', __pseudosnake_test_context__\n"
        "        )\n"
        "    else:\n"
        "        __pseudosnake_register__ = __builtins__._pseudosnake_register__\n"
        "        __pseudosnake_test_context__ = getattr(\n"
        "            __builtins__, '_pseudosnake_test_context__',\n"
        "            __pseudosnake_test_context__,\n"
        "        )\n"
        f"    __pseudosnake_register__({file_label!r}, __pseudosnake_cov__)\n"
        "except Exception:\n"
        "    pass\n\n"
    )


def _build_test_header_block() -> str:
    """Build the shared-context alias used by instrumented test functions."""
    return (
        "\n# PseudoSnake experimental dynamic coverage instrumentation\n"
        "# Use a local fallback only if the process bootstrap is unavailable.\n"
        "__pseudosnake_test_context__ = [None]\n"
        "try:\n"
        "    if isinstance(__builtins__, dict):\n"
        "        __pseudosnake_test_context__ = __builtins__.get(\n"
        "            '_pseudosnake_test_context__', __pseudosnake_test_context__\n"
        "        )\n"
        "    else:\n"
        "        __pseudosnake_test_context__ = getattr(\n"
        "            __builtins__, '_pseudosnake_test_context__',\n"
        "            __pseudosnake_test_context__,\n"
        "        )\n"
        "except Exception:\n"
        "    pass\n\n"
    )


def build_coverage_bootstrap() -> str:
    """Return ``sitecustomize`` code that owns process-wide coverage state."""
    return """import atexit as __pseudosnake_atexit__
import builtins as __pseudosnake_builtins__
import json as __pseudosnake_json__
import os as __pseudosnake_os__

# Every imported source module registers its own live counter dictionary here.
__pseudosnake_sources__ = {}
# Test markers and source counters share this mutable one-item context.
__pseudosnake_test_context__ = [None]

def __pseudosnake_register__(file_name, counters):
    __pseudosnake_sources__.setdefault(file_name, []).append(counters)

# Instrumented modules can reach the registrar without adding imports.
__pseudosnake_builtins__._pseudosnake_register__ = __pseudosnake_register__
__pseudosnake_builtins__._pseudosnake_test_context__ = __pseudosnake_test_context__

def __pseudosnake_flush_cov__():
    path = __pseudosnake_os__.getenv("PSEUDOSNAKE_DYN_COV_PATH")
    if not path:
        return
    try:
        # Preserve results already flushed by another interpreter process.
        data = {}
        if __pseudosnake_os__.path.exists(path):
            with open(path, "r", encoding="utf-8") as infile:
                data = __pseudosnake_json__.load(infile)
        for file_name, sources in __pseudosnake_sources__.items():
            file_data = data.setdefault(file_name, {})
            for counters in sources:
                for raw_key, count in counters.items():
                    # Current counters use ``(test_name, func_name)`` keys.
                    # Accept plain keys too so the bootstrap remains tolerant
                    # of counters registered without caller-aware tracking.
                    if isinstance(raw_key, tuple) and len(raw_key) == 2:
                        test_name, func_name = raw_key
                    else:
                        test_name, func_name = None, raw_key
                    # Keep the legacy nested report shape for compatibility.
                    current = file_data.get(func_name, {"total": 0, "by_test": {}})
                    if not isinstance(current, dict):
                        current = {"total": current, "by_test": {}}
                    current["total"] = current.get("total", 0) + count
                    if test_name is not None:
                        by_test = current.setdefault("by_test", {})
                        by_test[test_name] = by_test.get(test_name, 0) + count
                    file_data[func_name] = current
        with open(path, "w", encoding="utf-8") as outfile:
            __pseudosnake_json__.dump(data, outfile, indent=2)
    except Exception:
        # Coverage is best-effort and must never make the user's tests fail.
        return

# Flush after tests and imported modules have finished executing.
__pseudosnake_atexit__.register(__pseudosnake_flush_cov__)
"""


# safe insertion point


def _find_module_insert_index(lines: list[str]) -> int:
    """Return the line index after shebang, encoding, docstring, and future imports.

    The instrumentation header is injected here so it doesn't interfere
    with any of those module-level constructs.  Existing PseudoSnake
    instrumentation is also skipped to prevent duplication.
    """
    source = "".join(lines)
    try:
        # AST locations handle multiline strings and future imports more
        # reliably than scanning source text for quote characters.
        tree = ast.parse(source)
    except SyntaxError:
        # Instrumentation will later fail compilation with a useful error; an
        # end-of-file index keeps this helper safe for standalone callers.
        return len(lines)

    idx = 0
    # shebang and encoding declarations must remain at the physical top.
    if lines and lines[0].startswith("#!"):
        idx = 1
    if idx < len(lines) and "coding" in lines[idx]:
        idx += 1

    body_index = 0
    # module docstring must remain the first executable AST statement.
    if (
        tree.body
        and isinstance(tree.body[0], ast.Expr)
        and isinstance(tree.body[0].value, ast.Constant)
        and isinstance(tree.body[0].value.value, str)
    ):
        idx = max(idx, tree.body[0].end_lineno or tree.body[0].lineno)
        body_index = 1

    # __future__ imports are only valid near the beginning of the module.
    while body_index < len(tree.body):
        statement = tree.body[body_index]
        if not (
            isinstance(statement, ast.ImportFrom) and statement.module == "__future__"
        ):
            break
        idx = max(idx, statement.end_lineno or statement.lineno)
        body_index += 1

    return idx


# ---------------------------------------------------------------------------
# loading & querying coverage results
# ---------------------------------------------------------------------------


def load_dynamic_coverage_results(path: Path) -> dict:  # type: ignore[type-arg]
    """Load dynamic coverage JSON, returning ``{}`` on any error."""
    # file doesn't exist — nothing to load
    if not path.exists():
        return {}
    try:
        # attempt to parse the json file
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        # malformed json — ignore
        return {}
    # make sure we got a dict, not a list or scalar
    return loaded if isinstance(loaded, dict) else {}


def collect_function_coverage(
    file_path: Path,
    project_dir: Path,
    functions: list[FunctionInfo],
    coverage_results: dict,  # type: ignore[type-arg]
) -> dict[str, FunctionCoverage]:
    """Return normalized coverage records for one source file.

    Handles both the **legacy flat format** (``{func: int}``) and the
    **new nested caller-aware format** (``{func: {total, by_test}}``).
    Every discovered function receives a record, including functions with no
    calls, so report generation never has to guess whether data is missing.
    """
    # look up this file's coverage data by relative path
    relative = str(file_path.relative_to(project_dir))
    file_data = coverage_results.get(relative, {})
    if not isinstance(file_data, dict):
        file_data = {}
    coverage: dict[str, FunctionCoverage] = {}

    for func in functions:
        key = function_key(func)
        # older reports used only the qualified name, so accept that key while
        # reading existing coverage files created before line-aware identity.
        legacy_key = key.rsplit("@", 1)[0]
        value = file_data.get(key, file_data.get(legacy_key, 0))
        if isinstance(value, dict):
            # Keep only valid positive per-test counters. Sorting happens when
            # the record is serialized so output is deterministic.
            raw_by_test = value.get("by_test", {})
            by_test = (
                {
                    test_name: count
                    for test_name, count in raw_by_test.items()
                    if isinstance(test_name, str)
                    and isinstance(count, int)
                    and not isinstance(count, bool)
                    and count > 0
                }
                if isinstance(raw_by_test, dict)
                else {}
            )
            raw_total = value.get("total", 0)
            total = (
                raw_total
                if isinstance(raw_total, int)
                and not isinstance(raw_total, bool)
                and raw_total > 0
                else 0
            )
            # A per-test count is direct proof of execution even if an older or
            # partially written record omitted the matching total.
            total = max(total, sum(by_test.values()))
        else:
            # Legacy flat files contain only a total and cannot attribute it.
            total = (
                value
                if isinstance(value, int) and not isinstance(value, bool) and value > 0
                else 0
            )
            by_test = {}
        coverage[key] = FunctionCoverage(total_calls=total, by_test=by_test)

    return coverage
