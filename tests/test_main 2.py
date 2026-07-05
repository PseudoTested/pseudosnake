"""Tests for the PseudoSnake main CLI module."""

import re

from typer.testing import CliRunner

from pseudosnake.main import app

runner = CliRunner()
_ANSI_ESCAPE = re.compile(r"\x1b\[[0-9;]*m")


def _clean_output(result) -> str:
    """Return output with ANSI escape codes stripped."""
    return _ANSI_ESCAPE.sub("", result.output)


def test_cli_help() -> None:
    """Verify the CLI help message is displayed without errors."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    clean = _clean_output(result).lower()
    assert "analyse" in clean or "analyze" in clean


def test_analyze_help() -> None:
    """Verify the analyze sub-command exposes expected options."""
    result = runner.invoke(app, ["analyze", "--help"])
    assert result.exit_code == 0
    clean = _clean_output(result)
    assert "--project-dir" in clean
    assert "--test-command" in clean
    assert "--file" in clean
    assert "--source-dir" in clean
    assert "--num-test-runs" in clean
    assert "--output" in clean


def test_analyze_missing_args_shows_error() -> None:
    """analyze shows error message when required args are missing."""
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code != 0
    clean = _clean_output(result)
    assert "Missing option" in clean or "Error" in clean
