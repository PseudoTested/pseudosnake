"""Mutation strategy — generate and apply function body replacements.

PseudoSnake replaces the **entire body** of one precisely identified function
with a simple ``return`` statement. The standard AST identifies the function
and a targeted text edit preserves decorators, signatures, and surrounding
source.
"""

from __future__ import annotations

from collections.abc import Sequence

from pseudosnake.discover import FunctionInfo
from pseudosnake.source_edit import (
    SourceEditError,
    apply_text_edits,
    locate_function_nodes,
    replace_function_body,
)


MUTANTS_BY_TYPE: dict[str, list[str]] = {
    "int": ["return 0", "return 1"],
    "float": ["return 0.0", "return 1.0"],
    "str": ['return ""', 'return "A"'],
    "bool": ["return False", "return True"],
    "list": ["return []", "return None"],
    "dict": ["return {}", "return None"],
    "tuple": ["return ()", "return None"],
    "set": ["return set()", "return None"],
    "none": [],
    "optional": ["return None"],
    "any": ["return None"],
    "unknown": ["return None"],
}


class MutationApplicationError(ValueError):
    """Raised when a requested function cannot be mutated exactly once."""


def generate_mutants(return_type: str) -> list[str]:
    """Return the mutant return statements for *return_type*."""
    # unknown annotations use the conservative None-return fallback.
    return MUTANTS_BY_TYPE.get(return_type, ["return None"])


def apply_mutant(
    source_lines: Sequence[str],
    func_info: FunctionInfo,
    mutant: str,
) -> list[str]:
    """Return source lines with exactly one function body replaced safely.

    The target is matched using class name, function name, and the original
    ``def`` line. A mismatch is an invalid mutant rather than permission to
    alter a similarly named function.
    """
    source = "".join(source_lines)
    try:
        # resolve by class, name, and original def line before constructing a
        # targeted edit; this prevents same-named functions from being mixed up.
        node = locate_function_nodes(source, [func_info])[0]
        edit = replace_function_body(source, node, mutant)
        mutated = apply_text_edits(source, [edit])
    except (SyntaxError, SourceEditError) as exc:
        # the caller reports this as INVALID and does not start the test suite.
        raise MutationApplicationError(f"Could not apply mutation: {exc}") from exc
    return mutated.splitlines(keepends=True)
