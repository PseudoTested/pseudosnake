"""Tests for the PseudoSnake main CLI module."""

from typer.testing import CliRunner

from pseudosnake.main import app

runner = CliRunner()


def test_cli_help() -> None:
    """Verify the CLI help message is displayed without errors."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    assert "analyse" in result.output.lower() or "analyze" in result.output.lower()


def test_analyze_help() -> None:
    """Verify the analyze sub-command exposes expected options."""
    result = runner.invoke(app, ["analyze", "--help"])
    assert result.exit_code == 0
    assert "--project-dir" in result.output
    assert "--test-command" in result.output
    assert "--file" in result.output
    assert "--source-dir" in result.output
    assert "--num-test-runs" in result.output
    assert "--output" in result.output


def test_analyze_missing_args_shows_error() -> None:
    """analyze shows error message when required args are missing."""
    result = runner.invoke(app, ["analyze"])
    assert result.exit_code != 0
    assert "Missing option" in result.output or "Error" in result.output
