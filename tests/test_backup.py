"""Tests for pseudosnake.backup — file backup and restore."""

import tempfile
from pathlib import Path


from pseudosnake.backup import (
    backup_file,
    cleanup_backup,
    cleanup_snapshot,
    create_snapshot,
    get_backup_dir,
    list_snapshots,
    restore_file,
    restore_snapshot,
)


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


def test_create_snapshot_source_and_test_only(tmp_path: Path) -> None:
    """create_snapshot only backs up the files given — no extra files."""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("hello")
    (project / "README.md").write_text("# docs")

    snap_dir = create_snapshot([src / "mod.py"], project, "run_test")
    assert (snap_dir / "src" / "mod.py").read_text() == "hello"
    assert not (snap_dir / "README.md").exists()

    cleanup_snapshot(snap_dir)


def test_restore_snapshot_overwrites_files(tmp_path: Path) -> None:
    """restore_snapshot copies snapshot content back to original locations."""
    project = tmp_path / "project"
    src = project / "src"
    src.mkdir(parents=True)
    (src / "mod.py").write_text("original")

    snap_dir = create_snapshot([src / "mod.py"], project, "run_test")
    (src / "mod.py").write_text("modified")

    count = restore_snapshot(snap_dir, project)
    assert count == 1
    assert (src / "mod.py").read_text() == "original"

    cleanup_snapshot(snap_dir)


def test_cleanup_snapshot_removes_directory(tmp_path: Path) -> None:
    """cleanup_snapshot removes the snapshot directory tree."""
    project = tmp_path / "project"
    project.mkdir()
    (project / "mod.py").write_text("x")

    snap_dir = create_snapshot([project / "mod.py"], project, "run_test")
    assert snap_dir.exists()

    cleanup_snapshot(snap_dir)
    assert not snap_dir.exists()


def test_list_snapshots_empty(tmp_path: Path) -> None:
    """list_snapshots returns empty list when no snapshots exist."""
    result = list_snapshots(tmp_path)
    assert isinstance(result, list)


def test_revert_roundtrip_full(tmp_path: Path) -> None:
    """End-to-end: snapshot source+test files → corrupt → restore → verify."""
    project = tmp_path / "project"
    (project / "src").mkdir(parents=True)
    (project / "src" / "mod.py").write_text("import os\ndef f():\n    return 1\n")
    tests = project / "tests"
    tests.mkdir()
    (tests / "test_mod.py").write_text("def test_f():\n    assert True\n")

    snap_dir = create_snapshot(
        [project / "src" / "mod.py", tests / "test_mod.py"],
        project,
        "run_test",
    )

    (project / "src" / "mod.py").write_text("corrupted")
    (tests / "test_mod.py").write_text("corrupted")
    restore_snapshot(snap_dir, project)
    assert (
        project / "src" / "mod.py"
    ).read_text() == "import os\ndef f():\n    return 1\n"
    assert (tests / "test_mod.py").read_text() == "def test_f():\n    assert True\n"

    cleanup_snapshot(snap_dir)
