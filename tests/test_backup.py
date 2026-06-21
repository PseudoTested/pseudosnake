"""Tests for pseudosnake.backup — file backup and restore."""

import tempfile
from pathlib import Path


from pseudosnake.backup import backup_file, cleanup_backup, get_backup_dir, restore_file


def test_get_backup_dir_creates_and_returns_directory() -> None:
    """get_backup_dir returns a Path that exists on disk."""
    backup_dir = get_backup_dir()
    assert isinstance(backup_dir, Path)
    assert backup_dir.is_dir()


def test_backup_file_creates_copy() -> None:
    """backup_file creates a copy of the original in the backup directory."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write("original content")
        file_path = Path(tf.name)

    try:
        backup_path = backup_file(file_path)
        assert backup_path.exists()
        assert backup_path.read_text() == "original content"
        assert backup_path != file_path
    finally:
        file_path.unlink(missing_ok=True)
        cleanup_backup(backup_path)


def test_restore_file_overwrites_with_backup(tmp_path: Path) -> None:
    """restore_file replaces the target file with backup content."""
    original = tmp_path / "original.py"
    original.write_text("v1")
    backup_path = backup_file(original)

    original.write_text("v2")
    restore_file(original, backup_path)
    assert original.read_text() == "v1"

    cleanup_backup(backup_path)


def test_cleanup_backup_removes_file() -> None:
    """cleanup_backup deletes the backup file."""
    with tempfile.NamedTemporaryFile(mode="w", suffix=".py", delete=False) as tf:
        tf.write("temp")
        file_path = Path(tf.name)

    backup_path = backup_file(file_path)
    assert backup_path.exists()
    cleanup_backup(backup_path)
    assert not backup_path.exists()
    file_path.unlink(missing_ok=True)


def test_cleanup_backup_idempotent() -> None:
    """cleanup_backup does not raise when the file is already gone."""
    path = Path(tempfile.gettempdir()) / "nonexistent_backup.py"
    cleanup_backup(path)
