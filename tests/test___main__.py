"""Tests for the __main__ entry point."""

import runpy
import sys


def test_main_module_imports_and_runs() -> None:
    """python -m pseudosnake --help imports, runs, and exits 0."""
    saved = sys.argv
    try:
        sys.argv = ["pseudosnake", "--help"]
        runpy.run_module("pseudosnake.__main__", run_name="__main__")
    except SystemExit as exc:
        assert exc.code == 0
    finally:
        sys.argv = saved
