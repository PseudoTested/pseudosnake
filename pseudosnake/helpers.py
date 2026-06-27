"""Pure helper functions used by the main CLI — testable without importing Typer."""

import platform
import sys
from datetime import datetime
from hashlib import sha256
from pathlib import Path
from typing import Any

from pseudosnake.backup import get_backup_dir
from pseudosnake.discover import FunctionInfo


def build_uncovered_entry(func_info: FunctionInfo) -> dict[str, Any]:
    """Build a minimal report entry for a function that was never executed."""
    entry: dict[str, Any] = {
        "function_name": func_info.name,
        "line_number": func_info.line_number,
        "return_type": func_info.return_type,
        "covered": False,  # marks this function as not reached by any test
        "mutants": [],  # no mutants were run, so the list is empty
    }
    # include class name for methods
    if func_info.class_name is not None:
        entry["class_name"] = func_info.class_name
    return entry


def digest(content: str) -> str:
    """Return a SHA-256 hex digest of *content*.

    Used to verify that mutations were correctly applied and restored
    by comparing hashes of the original, mutated, and restored source.
    """
    return sha256(content.encode("utf-8")).hexdigest()


def build_metadata(
    start_time: datetime,
    end_time: datetime,
    project_dir: Path,
    source_dir: Path | None,
    file_arg: Path | None,
    output_file: Path | None,
    test_command: str,
    num_test_runs: int,
    dynamically_executed_functions: int,
    files_detected: int,
) -> dict[str, Any]:
    """Assemble the metadata section of the final JSON report."""
    return {
        "start_time": start_time.isoformat(),
        "end_time": end_time.isoformat(),
        "project_directory": str(project_dir),
        "source_directory": str(source_dir) if source_dir else None,
        "file_argument": str(file_arg) if file_arg else None,
        "output_file": str(output_file) if output_file else None,
        "test_command": test_command,
        "num_test_runs": num_test_runs,
        "dynamic_coverage_enabled": True,
        "dynamically_executed_functions": dynamically_executed_functions,
        "python_version": sys.version,
        "operating_system": platform.system(),
        "backup_directory": str(get_backup_dir()),
        "files_detected": files_detected,
    }
