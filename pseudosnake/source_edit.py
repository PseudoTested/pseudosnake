"""Targeted source edits guided by Python's standard :mod:`ast` module.

The AST supplies reliable function identities and source positions. Edits are
then applied only to the relevant source span, preserving unrelated comments,
formatting, decorators, and module contents.
"""

from __future__ import annotations

import ast
from dataclasses import dataclass

from pseudosnake.discover import FunctionInfo


# Synchronous and asynchronous definitions expose the same location fields,
# so the editing helpers can handle both through one shared type.
FunctionNode = ast.FunctionDef | ast.AsyncFunctionDef


class SourceEditError(ValueError):
    """Raised when a requested source transformation cannot be applied safely."""


@dataclass(frozen=True)
class TextEdit:
    """Replace ``source[start:end]`` with ``replacement``."""

    start: int
    end: int
    replacement: str


def _collect_function_nodes(
    node: ast.AST,
    class_name: str | None = None,
) -> list[tuple[tuple[str | None, str, int], FunctionNode]]:
    """Collect the same top-level functions and class methods as discovery."""
    found: list[tuple[tuple[str | None, str, int], FunctionNode]] = []
    for child in ast.iter_child_nodes(node):
        if isinstance(child, ast.ClassDef):
            # Carry the containing class name so methods have stable identities.
            found.extend(_collect_function_nodes(child, child.name))
        elif isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
            # The def line disambiguates repeated names and overload families.
            found.append(((class_name, child.name, child.lineno), child))
            # Nested functions are intentionally outside discovery's scope.
    return found


def locate_function_nodes(
    source: str,
    functions: list[FunctionInfo],
) -> list[FunctionNode]:
    """Resolve each discovered function to exactly one AST node."""
    # Parse once, then resolve all FunctionInfo records against the unchanged
    # tree so every edit uses positions from the same source version.
    tree = ast.parse(source)
    available = _collect_function_nodes(tree)
    resolved: list[FunctionNode] = []

    for function in functions:
        identity = (function.class_name, function.name, function.line_number)
        matches = [node for candidate, node in available if candidate == identity]
        # Refuse ambiguous or stale locations instead of mutating the wrong
        # same-named function.
        if len(matches) != 1:
            qualified = (
                function.name
                if function.class_name is None
                else f"{function.class_name}.{function.name}"
            )
            raise SourceEditError(
                f"Expected one match for {qualified} at line "
                f"{function.line_number}, found {len(matches)}"
            )
        resolved.append(matches[0])

    return resolved


def apply_text_edits(source: str, edits: list[TextEdit]) -> str:
    """Apply non-overlapping edits from the end of the source backwards."""
    # Validate all spans before changing source so a bad edit cannot leave a
    # partially transformed result.
    ordered = sorted(edits, key=lambda edit: (edit.start, edit.end))
    previous_end = -1
    for edit in ordered:
        if not 0 <= edit.start <= edit.end <= len(source):
            raise SourceEditError("Source edit is outside the source bounds")
        if edit.start < previous_end:
            raise SourceEditError("Source edits overlap")
        previous_end = edit.end

    result = source
    # Applying rightmost edits first preserves the offsets of earlier edits.
    for edit in reversed(ordered):
        result = result[: edit.start] + edit.replacement + result[edit.end :]
    return result


def _line_starts(source: str) -> list[int]:
    """Return the character offset where every source line begins."""
    starts = [0]
    starts.extend(index + 1 for index, char in enumerate(source) if char == "\n")
    return starts


def _position_offset(source: str, lineno: int, byte_column: int) -> int:
    """Convert an AST UTF-8 byte position into a character offset."""
    starts = _line_starts(source)
    try:
        line_start = starts[lineno - 1]
    except IndexError as exc:
        raise SourceEditError(f"Line {lineno} is outside the source") from exc

    line_end = starts[lineno] if lineno < len(starts) else len(source)
    line = source[line_start:line_end]
    try:
        # AST columns count UTF-8 bytes, while Python string slices count
        # Unicode characters; decoding the prefix safely bridges the two.
        character_column = len(line.encode("utf-8")[:byte_column].decode("utf-8"))
    except UnicodeDecodeError as exc:
        raise SourceEditError("AST position splits a UTF-8 character") from exc
    return line_start + character_column


def _line_start(source: str, lineno: int) -> int:
    """Return the character offset at the beginning of a one-based line."""
    starts = _line_starts(source)
    try:
        return starts[lineno - 1]
    except IndexError as exc:
        raise SourceEditError(f"Line {lineno} is outside the source") from exc


def _line_after(source: str, lineno: int) -> int:
    """Return the character offset immediately after a one-based line."""
    starts = _line_starts(source)
    return starts[lineno] if lineno < len(starts) else len(source)


def _indent_at(source: str, node: ast.stmt) -> str:
    """Return the exact whitespace preceding *node* on its source line."""
    position = _position_offset(source, node.lineno, node.col_offset)
    return source[_line_start(source, node.lineno) : position]


def _statement_start_line(node: ast.stmt) -> int:
    """Include decorators when locating the physical start of a statement."""
    if isinstance(node, (ast.ClassDef, ast.FunctionDef, ast.AsyncFunctionDef)):
        # Replacing a decorated nested definition must also remove decorators
        # that belong to that body statement.
        decorator_lines = [decorator.lineno for decorator in node.decorator_list]
        if decorator_lines:
            return min(decorator_lines)
    return node.lineno


def _leading_indent(source: str, lineno: int) -> str:
    """Return the leading spaces or tabs from a one-based source line."""
    start = _line_start(source, lineno)
    end = _line_after(source, lineno)
    line = source[start:end]
    return line[: len(line) - len(line.lstrip(" \t"))]


def _is_docstring(node: ast.stmt) -> bool:
    """Return whether *node* is a string expression used as a docstring."""
    return (
        isinstance(node, ast.Expr)
        and isinstance(node.value, ast.Constant)
        and isinstance(node.value.value, str)
    )


def _has_simple_suite(source: str, node: FunctionNode) -> bool:
    """Return whether the first body statement shares the header's final line."""
    first = node.body[0]
    position = _position_offset(source, first.lineno, first.col_offset)
    prefix = source[_line_start(source, first.lineno) : position]
    # A colon before the first statement on that same line is the function
    # header's suite separator, as in ``def answer(): return 42``.
    return ":" in prefix


def prepend_function_statement(
    source: str,
    node: FunctionNode,
    statement: str,
) -> TextEdit:
    """Build an edit that inserts a statement after any function docstring."""
    first = node.body[0]

    if _has_simple_suite(source, node):
        # A simple suite must remain on one physical line; semicolons insert
        # the counter without rewriting the function signature.
        if _is_docstring(first):
            if first.end_lineno is None or first.end_col_offset is None:
                raise SourceEditError("Function docstring has no end position")
            position = _position_offset(source, first.end_lineno, first.end_col_offset)
            return TextEdit(position, position, f"; {statement}")

        position = _position_offset(source, first.lineno, first.col_offset)
        return TextEdit(position, position, f"{statement}; ")

    indent = _indent_at(source, first)
    # Preserve CRLF when helpers are called directly with Windows-style text.
    newline = "\r\n" if "\r\n" in source else "\n"

    if _is_docstring(first):
        # Instrument after the docstring so Python still assigns __doc__.
        if len(node.body) > 1:
            anchor = node.body[1]
            anchor_line = _statement_start_line(anchor)
            position = _line_start(source, anchor_line)
            indent = _leading_indent(source, anchor_line)
        else:
            if first.end_lineno is None:
                raise SourceEditError("Function docstring has no end line")
            position = _line_after(source, first.end_lineno)
    else:
        first_line = _statement_start_line(first)
        position = _line_start(source, first_line)
        indent = _leading_indent(source, first_line)

    return TextEdit(position, position, f"{indent}{statement}{newline}")


def replace_function_body(
    source: str,
    node: FunctionNode,
    statement: str,
) -> TextEdit:
    """Build an edit replacing a function's complete executable body."""
    first = node.body[0]
    if node.end_lineno is None or node.end_col_offset is None:
        raise SourceEditError("Function has no end position")

    if _has_simple_suite(source, node):
        # For one-line definitions, replace only the text after the header.
        start = _position_offset(source, first.lineno, first.col_offset)
        end = _position_offset(source, node.end_lineno, node.end_col_offset)
        return TextEdit(start, end, statement)

    # Multiline bodies are replaced as complete physical lines. Including a
    # decorated first statement prevents orphaned decorators from remaining.
    start = _line_start(source, _statement_start_line(first))
    end = _line_after(source, node.end_lineno)
    indent = _indent_at(source, first)
    newline = "\r\n" if "\r\n" in source else "\n"
    return TextEdit(start, end, f"{indent}{statement}{newline}")
