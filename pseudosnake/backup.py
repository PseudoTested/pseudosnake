"""Backup and restore source files during mutation testing.

PseudoSnake mutates files in place — replacing function bodies with simple
return values — then runs the test suite.  Before any mutation the original
file is copied to a platform-appropriate data directory (managed by
``platformdirs``).  Afterwards the backup is restored and cleaned up.
"""

import shutil
import uuid
from pathlib import Path

from platformdirs import user_data_dir


def get_backup_dir() -> Path:
    """Return the platform-appropriate backup directory for PseudoSnake.
    Creates the directory if it does not already exist.
    """
    # resolve platform-specific data directory (macos, linux, windows)
    backup_dir = Path(user_data_dir("PseudoSnake"))
    # create it if it doesn't exist
    backup_dir.mkdir(parents=True, exist_ok=True)
    return backup_dir


def backup_file(file_path: Path) -> Path:
    """Copy *file_path* to the backup directory and return the backup path.

    A UUID prefix prevents name collisions when the same filename appears
    in different directories.
    """
    backup_dir = get_backup_dir()
    # use a uuid prefix so files with the same name from different dirs don't collide
    backup_name = f"{uuid.uuid4().hex}_{file_path.name}"
    backup_path = backup_dir / backup_name
    # copy preserving metadata (timestamps, permissions)
    shutil.copy2(file_path, backup_path)
    return backup_path


def restore_file(file_path: Path, backup_path: Path) -> None:
    """Overwrite *file_path* with the original content from *backup_path*."""
    # overwrite the mutated file with the original backup
    shutil.copy2(backup_path, file_path)


def cleanup_backup(backup_path: Path) -> None:
    """Delete the backup file (quietly no-ops if already gone)."""
    # missing_ok=True means no error if the file was already deleted
    backup_path.unlink(missing_ok=True)
