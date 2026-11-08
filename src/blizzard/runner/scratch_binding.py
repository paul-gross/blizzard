"""Host-local binding of active leases to the effective scratch root.

The caller holds the host lock and supplies runner-scoped lease observations.  The
old root is reconciled before the binding changes, so a crash can only cause that
reconciliation to be repeated.
"""

from __future__ import annotations

import os
import tempfile
from collections.abc import Callable, Mapping
from pathlib import Path

from blizzard.runner.host_layout import DATA_DIRNAME, LEGACY_WORKER_SCRATCH_DIRNAME
from blizzard.runner.leases.record import IReadLeaseRecordRepository
from blizzard.runner.process.internal.worker_scratch_dirs import WorkerScratchDirs

_BINDING = "worker-scratch-root"


def _check_root(root: Path) -> None:
    with os.scandir(root):
        pass
    fd, name = tempfile.mkstemp(prefix=".scratch-probe-", dir=root)
    os.close(fd)
    os.unlink(name)


def _sync_root_path(root: Path) -> None:
    """Make every directory entry leading to the root durable before binding it.

    Sync existing ancestors too: an earlier attempt may have created them and
    crashed before syncing their parents.
    """
    for directory in reversed((root, *root.parents)):
        fd = os.open(directory, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(fd)
        finally:
            os.close(fd)


def reconcile_scratch_roots(host_root: Path, root: Path, runners: Mapping[str, IReadLeaseRecordRepository]) -> None:
    """Bind and reconcile once, before any runner on this host can spawn.

    Each repository is already bound to its runner, including runners that could
    not be built. A previous root is swept before the binding is switched.
    """
    root = root.resolve()
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    _check_root(root)
    _sync_root_path(root)
    active = {runner: {lease.lease_id for lease in repo.list_active_leases()} for runner, repo in runners.items()}

    def reconcile(target: Path) -> None:
        _check_root(target)
        for runner, repo in runners.items():
            scratch = WorkerScratchDirs(
                str(target),
                runner_id=runner,
                legacy_root=str(host_root / LEGACY_WORKER_SCRATCH_DIRNAME),
            )
            scratch.sweep_orphans(active[runner], strict=True)

            def closed(lease_id: str, repo: IReadLeaseRecordRepository = repo, runner: str = runner) -> bool:
                return repo.lease(lease_id) is not None and lease_id not in active[runner]

            scratch.sweep_legacy(closed, strict=True)

    bind_scratch_root(host_root, root, active=any(active.values()), reconcile=reconcile)
    reconcile(root)


def bind_scratch_root(host_root: Path, root: Path, *, active: bool, reconcile: Callable[[Path], None]) -> None:
    """Bind ``root``, refusing changes until all this host's leases have drained.

    ``reconcile`` must finish safely or raise; the previous binding remains in
    place if it fails. The host lock serializes callers for this host.
    """
    root = root.resolve()
    data = host_root / DATA_DIRNAME
    data.mkdir(parents=True, exist_ok=True)
    binding = data / _BINDING
    try:
        previous = Path(binding.read_text(encoding="utf-8"))
    except FileNotFoundError:
        previous = None
    if previous is not None:
        if not previous.is_absolute() or "\x00" in str(previous):
            raise RuntimeError(f"Invalid scratch root binding in {binding}")
        if previous == root:
            return
        if active:
            raise RuntimeError(
                "Worker scratch root changed while leases are active; restore the old root and drain leases "
                "before editing the root"
            )
        reconcile(previous)
    fd, name = tempfile.mkstemp(prefix=f".{_BINDING}.", dir=data)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(str(root))
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, binding)
        dir_fd = os.open(data, os.O_RDONLY | os.O_DIRECTORY)
        try:
            os.fsync(dir_fd)
        finally:
            os.close(dir_fd)
    finally:
        if os.path.exists(name):
            os.unlink(name)
