"""Experimental dynamic function-coverage instrumentation.

How it works
------------
1. **Header block** — injected into every instrumented file (source and test).
   Sets up a shared ``__ps_ctx__`` list (stored on Python's ``builtins`` module
   so every file sees the same list) and a per-file ``__pseudosnake_cov__``
   counter dict.

2. **Test instrumentation** — a one-line marker ``__ps_ctx__[0] = "test_name"``
   is inserted at the top of every test function body.  This tells the shared
   context *which test is currently executing*.

3. **Source instrumentation** — a three-line counter is inserted at the top
   of every source function body.  It reads ``__ps_ctx__[0]``, builds a key
   like ``"test_x\\x00func_y"``, and increments that key in the per-file dict.

4. **Flush (atexit)** — after the test suite finishes, every file's
   ``__pseudosnake_flush_cov__`` function merges its raw counters into a
   shared JSON file, splitting each key on ``\\x00`` to build a nested
   ``{func: {total: n, by_test: {test: n}}}`` structure.

Why ``builtins``?
-----------------
Each Python source file is a separate module with its own global scope.
``__ps_ctx__ = [None]`` in file A is a *different list* from the same line
in file B.  Storing the context on ``builtins._pseudosnake_ctx`` guarantees
that **every** instrumented module references the same singleton list object.
"""

from __future__ import annotations

import json
from pathlib import Path

from pseudosnake.discover import FunctionInfo


# marker string injected into every instrumented file — used as an
# idempotency guard so re-instrumenting an already-instrumented file
# is a no-op
_INSTRUMENTATION_MARKER = "# PseudoSnake experimental dynamic coverage instrumentation"


def is_already_instrumented(source: str) -> bool:
    """Return True if *source* already contains PseudoSnake instrumentation."""
    return _INSTRUMENTATION_MARKER in source


# stable function identity


def function_key(func_info: FunctionInfo) -> str:
    """Return a stable function identifier for coverage records.

    Uses function name (and optional class name) — **not** line number, since
    line numbers shift after instrumentation injection.
    """
    # top-level functions are identified by just their name
    if func_info.class_name is None:
        return func_info.name
    # methods include the class name: "MyClass.my_method"
    return f"{func_info.class_name}.{func_info.name}"


# source-file instrumentation


def instrument_file_source(
    source: str,
    functions: list[FunctionInfo],
    file_label: str,
) -> str:
    """Return *source* with lightweight runtime counters injected.

    For each function a three-line tracking snippet is inserted at the top of
    its body.  A shared header block is inserted after the module docstring.
    """
    # nothing to instrument — return source unchanged
    if not functions:
        return source

    # idempotency guard: if already instrumented, return unchanged
    if _INSTRUMENTATION_MARKER in source:
        return source

    # split into lines, preserving trailing newlines
    lines = source.splitlines(keepends=True)
    instrumented = list(lines)

    # insert counter stanzas — process in reverse line order so earlier
    # insertions don't shift the line positions of later ones
    for func in sorted(functions, key=lambda f: f.body_start_line, reverse=True):
        indent = " " * func.body_col_offset  # match the function's indentation
        key = function_key(func)
        # three-line tracking snippet injected at the top of the function body.
        # wrapped in try/except so import-time execution (e.g. decorators)
        # doesn't poison coverage if the instrumentation context isn't ready:
        #   try:
        #       _t = __ps_ctx__[-1] if __ps_ctx__ else '_no_test_'
        #       _k = _t + '\x00' + "func_name"
        #       __pseudosnake_cov__[_k] = __pseudosnake_cov__.get(_k, 0) + 1
        #   except Exception:
        #       pass
        counter_stanza = (
            indent
            + "try:\n"
            + indent
            + "    _t = __ps_ctx__[-1] if __ps_ctx__ else '_no_test_'\n"
            + indent
            + "    _k = _t + '\\x00' + "
            + repr(key)
            + "\n"
            + indent
            + "    __pseudosnake_cov__[_k] = __pseudosnake_cov__.get(_k, 0) + 1\n"
            + indent
            + "except Exception:\n"
            + indent
            + "    pass\n"
        )
        # insert before the first body statement (1-indexed → 0-indexed)
        insert_at = func.body_start_line - 1
        instrumented[insert_at:insert_at] = [counter_stanza]

    # insert the module-level header (shared context + flush helper)
    # after the docstring and future imports
    header = _build_header_block(file_label)
    insert_index = _find_module_insert_index(lines)
    instrumented[insert_index:insert_index] = [header]
    return "".join(instrumented)


# test-file instrumentation


def instrument_test_file(
    source: str,
    test_functions: list[FunctionInfo],
    file_label: str,
) -> str:
    """Return *source* with ``__ps_ctx__.append("test_name")`` injected into tests.

    Each test function body starts with a line that pushes onto the shared
    context stack which test is currently executing.  When a source function is
    called from within that test, its counter will attribute the hit correctly.
    """
    if not test_functions:
        return source

    # idempotency guard: if already instrumented, return unchanged
    if _INSTRUMENTATION_MARKER in source:
        return source

    lines = source.splitlines(keepends=True)
    instrumented = list(lines)

    # inject marker at top of each test function body (reverse order for safety)
    for func in sorted(test_functions, key=lambda f: f.body_start_line, reverse=True):
        indent = " " * func.body_col_offset
        # push this test's name onto the shared context stack
        marker = indent + "__ps_ctx__.append(" + repr(func.name) + ")\n"
        insert_at = func.body_start_line - 1
        instrumented[insert_at:insert_at] = [marker]

    # also inject the shared header block (imports and atexit flush)
    header = _build_header_block(file_label)
    insert_index = _find_module_insert_index(lines)
    instrumented[insert_index:insert_index] = [header]
    return "".join(instrumented)


# header block (injected into every instrumented file)


def _build_header_block(file_label: str) -> str:
    """Build the module-level tracking infrastructure as a Python source string.

    Injects:
    * ``__ps_ctx__`` — alias to ``builtins._pseudosnake_ctx`` (shared list).
    * ``__pseudosnake_cov__`` — per-file counter dict.
    * ``__pseudosnake_flush_cov__()`` — atexit handler that merges counter
      data into the shared JSON file, converting raw ``test\\x00func`` keys
      into a nested ``{func: {total, by_test}}`` structure.
    """
    # fmt: off
    return (
        "\n# PseudoSnake experimental dynamic coverage instrumentation\n"
        "import atexit as __pseudosnake_atexit__\n"
        "import json as __pseudosnake_json__\n"
        "import os as __pseudosnake_os__\n"
        "import builtins as __ps_builtins__\n"
        "\n"
        # create a shared list on the builtins module — every file sees the
        # same list object, so test files can write to it and source files
        # can read from it
        "if not hasattr(__ps_builtins__, '_pseudosnake_ctx'):\n"
        "    __ps_builtins__._pseudosnake_ctx = []\n"
        "__ps_ctx__ = __ps_builtins__._pseudosnake_ctx\n"
        "\n"
        # per-file counter dict: maps "test_name\x00func_name" -> hit count
        "__pseudosnake_cov__ = {}\n"
        "\n"
        # atexit handler: merges this file's counters into the shared JSON
        "def __pseudosnake_flush_cov__() -> None:\n"
        "    path = __pseudosnake_os__.getenv('PSEUDOSNAKE_DYN_COV_PATH')\n"
        "    if not path:\n"
        "        return\n"
        "    try:\n"
        # load existing data if the JSON file already exists
        "        data = {}\n"
        "        if __pseudosnake_os__.path.exists(path):\n"
        "            with open(path, 'r', encoding='utf-8') as infile:\n"
        "                data = __pseudosnake_json__.load(infile)\n"
        # get or create this file's slot in the data dict
        "        file_data = data.setdefault(" + repr(file_label) + ", {})\n"
        # merge each counter entry
        "        for raw_key, count in __pseudosnake_cov__.items():\n"
        # split "test_name\x00func_name" into (test_name, func_name)
        "            parts = raw_key.split('\\x00', 1)\n"
        "            test_name = parts[0]\n"
        "            func_name = parts[1] if len(parts) > 1 else raw_key\n"
        # ensure the function slot exists in the new nested format
        "            if func_name not in file_data:\n"
        "                file_data[func_name] = {'total': 0, 'by_test': {}}\n"
        # handle legacy flat data (upgrade from int to nested dict)
        "            elif not isinstance(file_data[func_name], dict):\n"
        "                file_data[func_name] = {'total': file_data[func_name], 'by_test': {}}\n"
        # accumulate the total hit count
        "            file_data[func_name]['total'] = file_data[func_name].get('total', 0) + count\n"
        # if we know which test called, record it in the by_test map
        "            if test_name != '_no_test_':\n"
        "                by_test = file_data[func_name].setdefault('by_test', {})\n"
        "                by_test[test_name] = by_test.get(test_name, 0) + count\n"
        # write the merged data back to the JSON file
        "        with open(path, 'w', encoding='utf-8') as outfile:\n"
        "            __pseudosnake_json__.dump(data, outfile, indent=2)\n"
        "    except Exception:\n"
        "        return\n"
        "\n"
        # register the flush handler to run on normal process exit
        "__pseudosnake_atexit__.register(__pseudosnake_flush_cov__)\n\n"
    )
    # fmt: on


# safe insertion point


def _find_module_insert_index(lines: list[str]) -> int:
    """Return the line index after shebang, encoding, docstring, and future imports.

    The instrumentation header is injected here so it doesn't interfere
    with any of those module-level constructs.  Existing PseudoSnake
    instrumentation is also skipped to prevent duplication.
    """
    idx = 0

    # skip shebang line: #!/usr/bin/env python
    if lines and lines[0].startswith("#!"):
        idx = 1

    # skip encoding declaration: # -*- coding: utf-8 -*-
    if idx < len(lines) and "coding" in lines[idx]:
        idx += 1

    # skip module docstring (single-line or multi-line)
    if idx < len(lines) and lines[idx].lstrip().startswith(('"""', "'''")):
        quote = '"""' if '"""' in lines[idx] else "'''"
        if lines[idx].count(quote) >= 2:
            # single-line docstring: """summary."""
            idx += 1
        else:
            # multi-line docstring: skip until closing quote
            idx += 1
            while idx < len(lines) and quote not in lines[idx]:
                idx += 1
            if idx < len(lines):
                idx += 1  # skip the closing quote line

    # skip from __future__ import lines (must be at top of file)
    while idx < len(lines) and lines[idx].startswith("from __future__ import"):
        idx += 1

    # skip existing PseudoSnake instrumentation to prevent duplication
    # the header block starts with "\n# PseudoSnake ..." so the marker may
    # appear on lines[idx] (blank) + 1, or on lines[idx] directly if the
    # source has no leading blank line between the header and prior content
    marker_found = (idx < len(lines) and _INSTRUMENTATION_MARKER in lines[idx]) or (
        idx + 1 < len(lines) and _INSTRUMENTATION_MARKER in lines[idx + 1]
    )
    if marker_found:
        # skip past entire instrumentation block until the next blank line
        # followed by non-blank content (the original source continues there)
        while idx < len(lines):
            line = lines[idx].rstrip("\n").rstrip("\r")
            if line == "" and idx + 1 < len(lines) and lines[idx + 1].strip() != "":
                idx += 1
                break
            idx += 1

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


def collect_executed_function_keys(
    file_path: Path,
    project_dir: Path,
    functions: list[FunctionInfo],
    coverage_results: dict,  # type: ignore[type-arg]
) -> list[str]:
    """Return function keys with ``total > 0`` for a specific source file.

    Handles both the **legacy flat format** (``{func: int}``) and the
    **new nested caller-aware format** (``{func: {total, by_test}}``).
    """
    # look up this file's coverage data by relative path
    relative = str(file_path.relative_to(project_dir))
    file_data = coverage_results.get(relative, {})
    executed: list[str] = []

    for func in functions:
        key = function_key(func)
        value = file_data.get(key, 0)
        if isinstance(value, dict):
            # new nested format: check the "total" field
            if value.get("total", 0) > 0:
                executed.append(key)
        elif value > 0:
            # legacy flat format: the value itself is the count
            executed.append(key)

    return executed


def collect_test_to_function_map(
    file_path: Path,
    project_dir: Path,
    coverage_results: dict,  # type: ignore[type-arg]
) -> dict[str, set[str]]:
    """Return ``{test_name: {func_key, ...}}`` for a source file.

    Only populated when caller-aware instrumentation was used (nested format).
    """
    relative = str(file_path.relative_to(project_dir))
    file_data = coverage_results.get(relative, {})
    mapping: dict[str, set[str]] = {}

    # iterate over every function entry in this file's coverage data
    for func_key, value in file_data.items():
        # only nested entries have a "by_test" sub-dict
        if isinstance(value, dict) and "by_test" in value:
            # for each test that called this function, add the function key
            # to that test's set
            for test_name in value["by_test"]:
                mapping.setdefault(test_name, set()).add(func_key)

    return mapping
