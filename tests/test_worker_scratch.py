"""``WorkerScratchDirs`` — the per-lease scratch-directory layout (unit tier)."""

from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from blizzard.runner.process.worker_scratch import WorkerScratchDirs


@pytest.mark.unit
def test_path_is_rooted_and_keyed_on_lease_id(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    assert dirs.path("lease_1") == str(tmp_path / "lease_1")


@pytest.mark.unit
def test_path_is_empty_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    assert dirs.path("lease_1") == ""


@pytest.mark.unit
def test_ensure_creates_an_owner_only_directory(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    path = dirs.ensure("lease_1")

    assert path == str(tmp_path / "lease_1")
    assert os.path.isdir(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


@pytest.mark.unit
def test_ensure_is_idempotent(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    first = dirs.ensure("lease_1")
    (Path(first) / "notes.txt").write_text("draft")
    second = dirs.ensure("lease_1")

    assert second == first
    assert (Path(first) / "notes.txt").exists()  # a re-ensure never wipes what is already there


@pytest.mark.unit
def test_ensure_is_a_noop_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    assert dirs.ensure("lease_1") == ""


@pytest.mark.unit
def test_remove_deletes_the_directory_and_its_contents(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))
    path = Path(dirs.ensure("lease_1"))
    (path / "draft.md").write_text("scratch")

    dirs.remove("lease_1")

    assert not path.exists()


@pytest.mark.unit
def test_remove_of_a_missing_directory_never_raises(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    dirs.remove("lease_never_created")  # must not raise


@pytest.mark.unit
def test_remove_is_a_noop_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    dirs.remove("lease_1")  # must not raise


@pytest.mark.unit
def test_sweep_orphans_removes_everything_not_in_the_active_set(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))
    dirs.ensure("lease_active")
    dirs.ensure("lease_orphan")

    removed = dirs.sweep_orphans({"lease_active"})

    assert removed == 1
    assert os.path.isdir(dirs.path("lease_active"))
    assert not os.path.exists(dirs.path("lease_orphan"))


@pytest.mark.unit
def test_sweep_orphans_of_an_absent_root_is_a_noop(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path / "never-created"))

    assert dirs.sweep_orphans({"lease_active"}) == 0


@pytest.mark.unit
def test_sweep_orphans_is_a_noop_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    assert dirs.sweep_orphans({"lease_active"}) == 0
