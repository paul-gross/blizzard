from functools import partial
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import blizzard.runner.cli.scratch_binding as scratch_binding
from blizzard.runner.cli.scratch_binding import bind_scratch_root
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs

reconcile_scratch_roots = partial(scratch_binding.reconcile_scratch_roots, scratch_factory=WorkerScratchDirs)


def test_root_change_requires_drain_and_reconciles_before_switch(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    reconciled: list[Path] = []
    bind_scratch_root(tmp_path, old, active=True, reconcile=reconciled.append)
    bind_scratch_root(tmp_path, old, active=True, reconcile=reconciled.append)
    with pytest.raises(RuntimeError, match="drain leases"):
        bind_scratch_root(tmp_path, new, active=True, reconcile=reconciled.append)
    assert reconciled == []
    bind_scratch_root(tmp_path, new, active=False, reconcile=reconciled.append)
    assert reconciled == [old]
    bind_scratch_root(tmp_path, new, active=True, reconcile=reconciled.append)


def test_failed_reconciliation_does_not_switch_binding(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    bind_scratch_root(tmp_path, old, active=False, reconcile=lambda _: None)

    def fail(_: Path) -> None:
        raise OSError("interrupted")

    with pytest.raises(OSError, match="interrupted"):
        bind_scratch_root(tmp_path, new, active=False, reconcile=fail)
    with pytest.raises(RuntimeError, match="drain leases"):
        bind_scratch_root(tmp_path, new, active=True, reconcile=lambda _: None)


def test_root_sync_failure_never_publishes_binding_and_retry_syncs_existing_path(tmp_path: Path) -> None:
    root = tmp_path / "parent" / "scratch"
    binding = tmp_path / "data" / "worker-scratch-root"
    with (
        patch("blizzard.runner.cli.scratch_binding._sync_root_path", side_effect=OSError("interrupted")),
        pytest.raises(OSError, match="interrupted"),
    ):
        reconcile_scratch_roots(tmp_path, root, {})
    assert root.is_dir()
    assert not binding.exists()
    with patch(
        "blizzard.runner.cli.scratch_binding._sync_root_path",
        wraps=scratch_binding._sync_root_path,  # pyright: ignore[reportPrivateUsage]
    ) as sync:
        reconcile_scratch_roots(tmp_path, root, {})
    sync.assert_called_once_with(root)
    assert binding.read_text() == str(root)


def test_real_old_root_deletion_failure_prevents_switch(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    repo = MagicMock()
    repo.list_active_leases.return_value = []
    repo.lease.return_value = None
    runners = {"rn_1111ABCD": repo}
    reconcile_scratch_roots(tmp_path, old, runners)
    payload = Path(WorkerScratchDirs(str(old), "rn_1111ABCD").ensure("lease_1111WXYZ"))
    with (
        patch("blizzard.runner.process.internal.worker_scratch_dirs.shutil.rmtree", side_effect=OSError("denied")),
        pytest.raises(OSError, match="denied"),
    ):
        reconcile_scratch_roots(tmp_path, new, runners)
    tombstones = list((old / ".owners" / "r-abcd").glob(".deleting-*"))
    assert not payload.exists() and len(tombstones) == 1
    assert (tmp_path / "data" / "worker-scratch-root").read_text() == str(old)
    reconcile_scratch_roots(tmp_path, new, runners)
    assert not payload.exists() and not tombstones[0].exists()


def test_unavailable_old_root_prevents_switch(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    reconcile_scratch_roots(tmp_path, old, {})
    old.rename(tmp_path / "offline")
    with pytest.raises(FileNotFoundError):
        reconcile_scratch_roots(tmp_path, new, {})
    assert (tmp_path / "data" / "worker-scratch-root").read_text() == str(old)


@pytest.mark.parametrize("mode", [0o500, 0o300])
def test_startup_rejects_unusable_root(tmp_path: Path, mode: int) -> None:
    root = tmp_path / "scratch"
    root.mkdir()
    root.chmod(mode)
    try:
        with pytest.raises(PermissionError):
            reconcile_scratch_roots(tmp_path, root, {})
    finally:
        root.chmod(0o700)


def test_real_cleanup_permission_failure_preserves_binding(tmp_path: Path) -> None:
    old, new = tmp_path / "old", tmp_path / "new"
    repo = MagicMock()
    repo.list_active_leases.return_value = []
    runners = {"rn_1111ABCD": repo}
    reconcile_scratch_roots(tmp_path, old, runners)
    payload = Path(WorkerScratchDirs(str(old), "rn_1111ABCD").ensure("lease_1111WXYZ"))
    (payload / "file").write_text("data")
    payload.chmod(0o500)
    try:
        with pytest.raises(PermissionError):
            reconcile_scratch_roots(tmp_path, new, runners)
        assert (tmp_path / "data" / "worker-scratch-root").read_text() == str(old)
        assert (payload / "file").exists()
    finally:
        payload.chmod(0o700)
    reconcile_scratch_roots(tmp_path, new, runners)
    assert not payload.exists()


def test_host_reconciliation_aggregates_runners_before_changing_root(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    adapters: list[tuple[str, str, MagicMock]] = []

    def adapter(root: str, *, runner_id: str, legacy_root: str) -> MagicMock:
        assert legacy_root == str(tmp_path / "worker-tmp")
        instance = MagicMock()
        adapters.append((root, runner_id, instance))
        return instance

    reconcile_scratch_roots = partial(scratch_binding.reconcile_scratch_roots, scratch_factory=adapter)
    first, second = MagicMock(), MagicMock()
    first.list_active_leases.return_value = []
    second.list_active_leases.return_value = [SimpleNamespace(lease_id="active")]

    first.closed_lease_ids.return_value = {"closed"}
    old, new = tmp_path / "old", tmp_path / "new"
    reconcile_scratch_roots(tmp_path, old, {"a": first, "b": second})
    adapters.clear()
    with pytest.raises(RuntimeError, match="drain leases"):
        reconcile_scratch_roots(tmp_path, new, {"a": first, "b": second})
    assert not adapters
    second.list_active_leases.return_value = []
    reconcile_scratch_roots(tmp_path, new, {"a": first, "b": second})
    assert [(root, runner) for root, runner, _ in adapters] == [
        (str(old), "a"),
        (str(old), "b"),
        (str(new), "a"),
        (str(new), "b"),
    ]
    closed = adapters[0][2].sweep_legacy.call_args.args[0]
    assert closed("closed")
    assert not closed("unknown")
