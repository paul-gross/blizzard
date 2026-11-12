"""``WorkerScratchDirs`` — the per-lease scratch-directory layout (unit tier)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from unittest.mock import patch

import pytest

from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs


def test_path_is_rooted_and_keyed_on_lease_id(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    assert dirs.path("lease_1") == str(tmp_path / "lease_1")


def test_path_is_empty_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    assert dirs.path("lease_1") == ""


def test_ensure_creates_an_owner_only_directory(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    path = dirs.ensure("lease_1")

    assert path == str(tmp_path / "lease_1")
    assert os.path.isdir(path)
    assert stat.S_IMODE(os.stat(path).st_mode) == 0o700


def test_ensure_is_idempotent(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    first = dirs.ensure("lease_1")
    (Path(first) / "notes.txt").write_text("draft")
    second = dirs.ensure("lease_1")

    assert second == first
    assert (Path(first) / "notes.txt").exists()  # a re-ensure never wipes what is already there


def test_ensure_is_a_noop_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    assert dirs.ensure("lease_1") == ""


def test_remove_deletes_the_directory_and_its_contents(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))
    path = Path(dirs.ensure("lease_1"))
    (path / "draft.md").write_text("scratch")

    dirs.remove("lease_1")

    assert not path.exists()


def test_remove_of_a_missing_directory_never_raises(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))

    dirs.remove("lease_never_created")  # must not raise


def test_remove_is_a_noop_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    dirs.remove("lease_1")  # must not raise


def test_sweep_orphans_without_a_runner_id_deletes_nothing(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path))
    dirs.ensure("lease_active")
    dirs.ensure("lease_orphan")

    removed = dirs.sweep_orphans({"lease_active"})

    assert removed == 0
    assert os.path.isdir(dirs.path("lease_active"))
    assert os.path.isdir(dirs.path("lease_orphan"))


def test_sweep_orphans_removes_every_owned_entry_not_in_the_active_set(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), runner_id="rn_01M3MY1NGQR8WE1ZBRKNREFNE4")
    dirs.ensure("lease_active")
    dirs.ensure("lease_orphan")

    removed = dirs.sweep_orphans({"lease_active"})

    assert removed == 1
    assert os.path.isdir(dirs.path("lease_active"))
    assert not os.path.exists(dirs.path("lease_orphan"))


def test_sweep_orphans_of_an_absent_root_is_a_noop(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path / "never-created"))

    assert dirs.sweep_orphans({"lease_active"}) == 0


def test_sweep_orphans_is_a_noop_when_disabled() -> None:
    dirs = WorkerScratchDirs("")

    assert dirs.sweep_orphans({"lease_active"}) == 0


def test_sweep_flat_entries_removes_only_accepted_names_and_never_a_runner_subtree(tmp_path: Path) -> None:
    for name in ("lease_old", "lease_other", "rn_x"):
        (tmp_path / name).mkdir()
    dirs = WorkerScratchDirs(str(tmp_path))

    removed = dirs.sweep_flat_entries(lambda name: name != "lease_other" and name != "never")

    assert removed == 1
    assert not (tmp_path / "lease_old").exists()
    assert (tmp_path / "lease_other").exists()
    assert (tmp_path / "rn_x").exists()


def test_compact_collisions_are_stable_across_restart_and_removal(tmp_path: Path) -> None:
    first = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    second_runner = WorkerScratchDirs(str(tmp_path), "rn_2222ABCD")
    a = first.ensure("lease_1111WXYZ")
    b = first.ensure("lease_2222WXYZ")
    c = second_runner.ensure("lease_3333WXYZ")
    assert Path(a).relative_to(tmp_path).as_posix() == "r-abcd/l-wxyz"
    assert Path(b).name == "l-2wxyz"
    assert Path(c).parent.name == "r-2abcd"
    first.remove("lease_1111WXYZ")
    assert WorkerScratchDirs(str(tmp_path), "rn_1111ABCD").path("lease_2222WXYZ") == b
    assert WorkerScratchDirs(str(tmp_path), "rn_2222ABCD").path("lease_3333WXYZ") == c


def test_reservation_survives_missing_payload_and_sweep_is_owned(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    reserved = dirs.path("lease_1111WXYZ")
    assert not Path(reserved).exists()
    assert dirs.ensure("lease_1111WXYZ") == reserved
    other = WorkerScratchDirs(str(tmp_path), "rn_2222ABCD")
    foreign = Path(other.ensure("lease_2222WXYZ"))
    orphan = Path(dirs.ensure("lease_3333WXYZ"))
    unknown = orphan.parent / "unknown"
    unknown.mkdir()
    assert dirs.sweep_orphans({"lease_1111WXYZ"}) == 1
    assert not orphan.exists()
    assert foreign.exists() and unknown.exists() and Path(reserved).exists()


def test_interrupted_deletion_retains_record(tmp_path: Path, monkeypatch: object) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    with patch("blizzard.runner.process.internal.worker_scratch_dirs.shutil.rmtree", side_effect=OSError):
        dirs.remove("lease_1111WXYZ")
    assert not path.exists()
    assert dirs.path("lease_1111WXYZ") == str(path)
    assert dirs.sweep_orphans(set()) == 1
    assert not path.exists()


def test_worker_can_remove_marker_without_breaking_resume_or_cleanup(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    (path / ".owner").unlink()
    (path / "data").write_text("scratch")
    assert dirs.ensure("lease_1111WXYZ") == str(path)
    assert dirs.sweep_orphans({"lease_1111WXYZ"}, strict=True) == 0
    assert dirs.sweep_orphans(set(), strict=True) == 1
    assert not path.exists()


def test_missing_marker_does_not_adopt_replacement(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    path.rename(path.with_name("displaced"))
    path.mkdir()
    (path / "sentinel").write_text("foreign")
    with pytest.raises(OSError, match="ownership mismatch"):
        dirs.ensure("lease_1111WXYZ")
    with pytest.raises(OSError, match="Cannot remove"):
        dirs.sweep_orphans(set(), strict=True)
    assert (path / "sentinel").read_text() == "foreign"


def test_missing_payload_is_recreated_and_removed(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    displaced = path.with_name("displaced")
    path.rename(displaced)
    assert dirs.ensure("lease_1111WXYZ") == str(path)
    assert dirs.ensure("lease_1111WXYZ") == str(path)
    dirs.remove("lease_1111WXYZ")
    assert not path.exists() and displaced.exists()


@pytest.mark.parametrize("after_publish", [False, True])
def test_recreation_interruption_recovers(tmp_path: Path, after_publish: bool) -> None:
    from blizzard.runner.process.internal import worker_scratch_dirs as scratch

    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    path.rename(path.with_name("displaced"))
    publish = scratch._publish_directory  # pyright: ignore[reportPrivateUsage]

    def interrupt(target: Path, token: str) -> None:
        if target == path:
            if after_publish:
                publish(target, token)
            raise OSError("interrupted")
        publish(target, token)

    with patch.object(scratch, "_publish_directory", interrupt), pytest.raises(OSError, match="interrupted"):
        dirs.ensure("lease_1111WXYZ")
    restarted = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    assert restarted.ensure("lease_1111WXYZ") == str(path)
    assert restarted.ensure("lease_1111WXYZ") == str(path)
    assert restarted.sweep_orphans(set(), strict=True) == 1
    assert not path.exists()


def test_recreation_does_not_readopt_displaced_payload_after_interruption(tmp_path: Path) -> None:
    from blizzard.runner.process.internal import worker_scratch_dirs as scratch

    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    displaced = path.with_name("displaced")
    (path / "sentinel").write_text("data")
    path.rename(displaced)
    publish = scratch._publish_directory  # pyright: ignore[reportPrivateUsage]

    def interrupt(target: Path, token: str) -> None:
        if target == path:
            raise OSError("interrupted")
        publish(target, token)

    with patch.object(scratch, "_publish_directory", interrupt), pytest.raises(OSError, match="interrupted"):
        dirs.ensure("lease_1111WXYZ")
    displaced.rename(path)
    with pytest.raises(OSError, match="ownership mismatch"):
        dirs.ensure("lease_1111WXYZ")
    assert dirs.sweep_orphans(set()) == 0
    assert (path / "sentinel").read_text() == "data"


def test_deletion_resumes_after_marker_is_removed(tmp_path: Path) -> None:
    import shutil

    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    (path / "data").write_text("scratch")
    original = shutil.rmtree

    def interrupt(target: str | Path, *args: object, **kwargs: object) -> None:
        Path(target, ".owner").unlink()
        raise OSError("interrupted")

    with patch("blizzard.runner.process.internal.worker_scratch_dirs.shutil.rmtree", interrupt):
        dirs.remove("lease_1111WXYZ")
    tombstones = list((tmp_path / ".owners" / "r-abcd").glob(".deleting-*"))
    assert len(tombstones) == 1 and (tombstones[0] / "data").exists()
    path.mkdir()
    (path / "sentinel").write_text("foreign")
    assert WorkerScratchDirs(str(tmp_path), "rn_1111ABCD").sweep_orphans(set(), strict=True) == 1
    assert (path / "sentinel").read_text() == "foreign"
    assert not tombstones[0].exists()
    original(path)


def test_deletion_resumes_after_proof_before_rename(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    with patch("blizzard.runner.process.internal.worker_scratch_dirs.os.rename", side_effect=OSError("crash")):
        dirs.remove("lease_1111WXYZ")
    assert path.exists()
    assert WorkerScratchDirs(str(tmp_path), "rn_1111ABCD").sweep_orphans(set(), strict=True) == 1
    assert not path.exists()


def test_deletion_refuses_replaced_payload_after_proof(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    with patch("blizzard.runner.process.internal.worker_scratch_dirs.os.rename", side_effect=OSError("crash")):
        dirs.remove("lease_1111WXYZ")
    path.rename(path.with_name("displaced"))
    path.mkdir()
    (path / "sentinel").write_text("foreign")
    assert dirs.sweep_orphans(set()) == 0
    assert (path / "sentinel").read_text() == "foreign"


def test_deletion_refuses_replaced_tombstone(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.ensure("lease_1111WXYZ"))
    with patch("blizzard.runner.process.internal.worker_scratch_dirs.shutil.rmtree", side_effect=OSError("crash")):
        dirs.remove("lease_1111WXYZ")
    tombstone = next((tmp_path / ".owners" / "r-abcd").glob(".deleting-*"))
    tombstone.rename(path)
    tombstone.mkdir()
    (tombstone / "sentinel").write_text("foreign")
    assert dirs.sweep_orphans(set()) == 0
    assert (tombstone / "sentinel").read_text() == "foreign"


def test_interrupted_creation_keeps_published_reservation(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    from blizzard.runner.process.internal import worker_scratch_dirs as scratch

    original = scratch._publish_directory  # pyright: ignore[reportPrivateUsage]

    def interrupt(path: Path, token: str) -> None:
        if path.name == "l-wxyz":
            raise OSError("interrupted")
        original(path, token)

    with patch.object(scratch, "_publish_directory", interrupt), pytest.raises(OSError, match="interrupted"):
        dirs.ensure("lease_1111WXYZ")
    restarted = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    assert restarted.ensure("lease_1111WXYZ") == str(tmp_path / "r-abcd" / "l-wxyz")
    assert restarted.sweep_orphans(set()) == 1


def test_interrupted_deletion_after_payload_removal_is_recoverable(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    payload = Path(dirs.ensure("lease_1111WXYZ"))
    original = Path.unlink

    def interrupt(path: Path, missing_ok: bool = False) -> None:
        if path.name == "l-wxyz.json":
            raise OSError("interrupted")
        original(path, missing_ok=missing_ok)

    with patch.object(Path, "unlink", interrupt):
        dirs.remove("lease_1111WXYZ")
    assert not payload.exists()
    restarted = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    assert restarted.sweep_orphans(set()) == 1


def test_ownership_directory_synced_before_payload_creation(tmp_path: Path) -> None:
    import blizzard.runner.process.internal.worker_scratch_dirs as scratch

    events: list[tuple[str, Path]] = []
    sync, publish = scratch._sync_directory, scratch._publish_directory  # pyright: ignore[reportPrivateUsage]

    def record_sync(path: Path) -> None:
        events.append(("sync", path))
        sync(path)

    def record_publish(path: Path, token: str) -> None:
        events.append(("publish", path))
        publish(path, token)

    with (
        patch.object(scratch, "_sync_directory", record_sync),
        patch.object(scratch, "_publish_directory", record_publish),
    ):
        WorkerScratchDirs(str(tmp_path), "rn_1111ABCD").ensure("lease_1111WXYZ")
    assert events.index(("sync", tmp_path / ".owners")) < events.index(("publish", tmp_path / "r-abcd"))
    assert events.index(("sync", tmp_path / ".owners" / "r-abcd")) < events.index(
        ("publish", tmp_path / "r-abcd" / "l-wxyz")
    )


def test_legacy_reuse_and_proven_cleanup(tmp_path: Path) -> None:
    old = tmp_path / "worker-tmp"
    keyed = old / "rn_1111ABCD" / "lease_1111WXYZ"
    flat = old / "lease_2222WXYZ"
    for path in (keyed, flat):
        path.mkdir(parents=True)
    dirs = WorkerScratchDirs(str(tmp_path / "tmp"), "rn_1111ABCD", str(old), lambda _: True)
    assert dirs.ensure("lease_1111WXYZ") == str(keyed)
    assert dirs.ensure("lease_2222WXYZ") == str(flat)
    assert dirs.sweep_legacy(lambda lease: lease == "lease_2222WXYZ") == 1
    assert keyed.exists() and not flat.exists()


def test_unowned_legacy_payload_is_neither_reused_nor_removed(tmp_path: Path) -> None:
    old = tmp_path / "worker-tmp"
    flat = old / "lease_1111WXYZ"
    flat.mkdir(parents=True)
    (flat / "sentinel").write_text("foreign")
    dirs = WorkerScratchDirs(str(tmp_path / "tmp"), "rn_1111ABCD", str(old))
    allocated = Path(dirs.ensure("lease_1111WXYZ"))
    assert allocated != flat
    dirs.remove("lease_1111WXYZ")
    assert not allocated.exists()
    assert (flat / "sentinel").read_text() == "foreign"


def test_symlink_payload_is_not_followed_or_removed(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path / "scratch"), "rn_1111ABCD")
    path = Path(dirs.path("lease_1111WXYZ"))
    path.parent.mkdir()
    target = tmp_path / "foreign"
    target.mkdir()
    path.symlink_to(target, target_is_directory=True)
    dirs.remove("lease_1111WXYZ")
    assert path.is_symlink() and target.exists()
    assert dirs.sweep_orphans(set()) == 0


def test_default_socket_budget(tmp_path: Path) -> None:
    host = Path("/" + "x" * 39)
    scratch = host / "tmp" / "r-abcd" / "l-wxyz"
    socket = scratch / ("x" * 24) / ".s.PGSQL.54321"
    assert len(os.fsencode(socket)) <= 107


def test_foreign_payload_after_reservation_is_never_adopted_or_deleted(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.path("lease_1111WXYZ"))
    # Publish the runner legitimately, then replace the reserved lease with foreign data.
    dirs.ensure("lease_2222ZZZZ")
    path.mkdir()
    (path / "sentinel").write_text("foreign")
    with pytest.raises(OSError, match="ownership mismatch"):
        dirs.ensure("lease_1111WXYZ")
    dirs.remove("lease_1111WXYZ")
    assert dirs.sweep_orphans({"lease_2222ZZZZ"}) == 0
    assert (path / "sentinel").read_text() == "foreign"
    assert dirs.path("lease_1111WXYZ") == str(path)


def test_foreign_runner_directory_after_reservation_is_not_adopted(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    path = Path(dirs.path("lease_1111WXYZ"))
    path.parent.mkdir()
    (path.parent / "sentinel").write_text("foreign")
    with pytest.raises(OSError, match="ownership mismatch"):
        dirs.ensure("lease_1111WXYZ")
    dirs.remove("lease_1111WXYZ")
    assert (path.parent / "sentinel").read_text() == "foreign"


def test_foreign_bookkeeping_after_reservation_is_not_adopted(tmp_path: Path) -> None:
    dirs = WorkerScratchDirs(str(tmp_path), "rn_1111ABCD")
    dirs.path("lease_1111WXYZ")
    records = tmp_path / ".owners" / "r-abcd"
    import shutil

    shutil.rmtree(records)
    records.mkdir()
    (records / "sentinel").write_text("foreign")
    with pytest.raises(OSError, match="ownership mismatch"):
        dirs.path("lease_1111WXYZ")
    dirs.remove("lease_1111WXYZ")
    assert (records / "sentinel").read_text() == "foreign"
