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
    """Verify the CLI help message lists available subcommands."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    clean = _clean_output(result).lower()
    assert "pseudosnake" in clean or "pseudo-tested" in clean


def test_default_help_shows_options() -> None:
    """Default --help shows analyze options as top-level args."""
    result = runner.invoke(app, ["--help"])
    assert result.exit_code == 0
    clean = _clean_output(result)
    assert "--project-dir" in clean
    assert "--test-command" in clean
    assert "--file" in clean
    assert "--source-dir" in clean
    assert "--num-test-runs" in clean
    assert "--output" in clean
    assert "--revert" in clean


def test_running_without_args_shows_help() -> None:
    """Running with no arguments shows the help text."""
    result = runner.invoke(app, [])
    assert result.exit_code == 0
    clean = _clean_output(result)
    assert "--project-dir" in clean


def test_revert_no_snapshots(tmp_path, monkeypatch) -> None:
    """--revert reports nothing to do when no snapshots exist."""
    monkeypatch.setattr("pseudosnake.main.list_snapshots", lambda pd: [])
    result = runner.invoke(app, ["--revert", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    clean = _clean_output(result)
    assert "No snapshots found" in clean


def test_revert_restores_from_snapshot(tmp_path, monkeypatch) -> None:
    """revert restores files from the latest snapshot and cleans up."""
    from pathlib import Path

    snapshot_dir = tmp_path / "fake_snapshots" / "run_20250101_120000"
    snapshot_dir.mkdir(parents=True)
    (snapshot_dir / "mod.py").write_text("original content")

    monkeypatch.setattr(
        "pseudosnake.main.list_snapshots",
        lambda pd: [snapshot_dir],
    )

    def fake_restore(snap_dir, proj_dir):
        from shutil import copy2

        for f in snap_dir.rglob("*"):
            if f.is_file():
                rel = f.relative_to(snap_dir)
                dest = Path(proj_dir) / rel
                dest.parent.mkdir(parents=True, exist_ok=True)
                copy2(str(f), str(dest))
        return 1

    monkeypatch.setattr("pseudosnake.main.restore_snapshot", fake_restore)
    monkeypatch.setattr("pseudosnake.main.cleanup_snapshot", lambda d: None)

    (tmp_path / "mod.py").write_text("modified content")
    result = runner.invoke(app, ["--revert", "--project-dir", str(tmp_path)])
    assert result.exit_code == 0
    clean = _clean_output(result)
    assert "1 file(s) restored" in clean
    assert "Snapshots cleaned up" in clean
    assert (tmp_path / "mod.py").read_text() == "original content"
