"""Backup and restore source files during mutation testing.

PseudoSnake mutates files in place — replacing function bodies with simple
return values — then runs the test suite.  Before any mutation the original
file is copied to a platform-appropriate data directory (managed by
``platformdirs``).  Afterwards the backup is restored and cleaned up.
"""

import hashlib
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


def _project_key(project_dir: Path) -> str:
    """Return a short stable identifier for *project_dir*."""
    resolved = str(project_dir.resolve())
    return hashlib.sha256(resolved.encode()).hexdigest()[:12]


def get_snapshot_dir(project_dir: Path, snapshot_id: str) -> Path:
    """Return the platform-appropriate path for a snapshot scoped to *project_dir*."""
    return get_backup_dir() / "snapshots" / _project_key(project_dir) / snapshot_id


def create_snapshot(files: list[Path], project_dir: Path, snapshot_id: str) -> Path:
    """Snapshot *files* (relative to *project_dir*) into a run-specific directory.

    Each file is copied into the snapshot directory, preserving its relative
    path from *project_dir*.  Returns the path to the snapshot directory.
    """
    snapshot_dir = get_snapshot_dir(project_dir, snapshot_id)
    snapshot_dir.mkdir(parents=True, exist_ok=True)
    for file_path in files:
        relative = file_path.relative_to(project_dir)
        dest = snapshot_dir / relative
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(file_path, dest)
    return snapshot_dir


def restore_snapshot(snapshot_dir: Path, project_dir: Path) -> int:
    """Restore all files from *snapshot_dir* back to *project_dir*.

    Returns the number of files restored.
    """
    count = 0
    for backup_file in sorted(snapshot_dir.rglob("*")):
        if backup_file.is_file():
            relative = backup_file.relative_to(snapshot_dir)
            original = project_dir / relative
            shutil.copy2(backup_file, original)
            count += 1
    return count


def cleanup_snapshot(snapshot_dir: Path) -> None:
    """Delete the snapshot directory and all contents."""
    shutil.rmtree(snapshot_dir, ignore_errors=True)


def list_snapshots(project_dir: Path) -> list[Path]:
    """Return all snapshot directories for *project_dir*, sorted newest first."""
    project_root = get_snapshot_dir(project_dir, "")
    if not project_root.is_dir():
        return []
    return sorted(
        [p for p in project_root.iterdir() if p.is_dir()],
        reverse=True,
    )
