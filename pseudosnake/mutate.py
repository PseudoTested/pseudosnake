"""Mutation strategy — generate and apply function body replacements.

PseudoSnake replaces the **entire body** of a function with a simple
``return`` statement whose value depends on the function's annotated return
type.  For example, ``def add(...) -> int: ...``  becomes ``return 0`` and
``return 1`` — two separate mutants.
"""

from pseudosnake.discover import FunctionInfo


# mutant catalogue
# every known return type maps to a list of replacement return statements
# types without an explicit entry (or "unknown") fall back to ["return None"]

MUTANTS_BY_TYPE: dict[str, list[str]] = {
    "int": ["return 0", "return 1"],
    "float": ["return 0.0", "return 1.0"],
    "str": ['return ""', 'return "A"'],
    "bool": ["return False", "return True"],
    "list": ["return []", "return None"],
    "dict": ["return {}", "return None"],
    "tuple": ["return ()", "return None"],
    "set": ["return set()", "return None"],
    "none": [],  # function already returns None — no meaningful mutant
    "optional": ["return None"],
    "any": ["return None"],
    "unknown": ["return None"],
}


def generate_mutants(return_type: str) -> list[str]:
    """Return the mutant return statements for *return_type*.

    Falls back to ``["return None"]`` for unrecognised types.
    """
    # look up the return type in the catalogue, default to ["return None"]
    return MUTANTS_BY_TYPE.get(return_type, ["return None"])


def apply_mutant(
    source_lines: list[str],
    func_info: FunctionInfo,
    mutant: str,
) -> list[str]:
    """Return a new source-line list with the function body replaced.

    The function signature (``def`` line) is preserved.  Everything from the
    first body statement through the closing line of the function is replaced
    with a single indented ``return`` statement.
    """
    # build the replacement line with correct indentation
    indent = " " * func_info.body_col_offset
    replacement = indent + mutant + "\n"

    # lines before the first body statement (def + decorators + docstring)
    before = source_lines[: func_info.body_start_line - 1]
    # lines after the function's closing line (following functions/trailing code)
    after = source_lines[func_info.end_line :]

    # splice: keep the signature, replace the body, keep trailing code
    return before + [replacement] + after
