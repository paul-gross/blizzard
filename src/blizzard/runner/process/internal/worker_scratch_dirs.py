"""Filesystem-backed, runner-scoped scratch reservations and payloads."""

from __future__ import annotations

import contextlib
import ctypes
import errno
import fcntl
import json
import os
import secrets
import shutil
import stat
import tempfile
from collections.abc import Callable, Generator, Iterable
from dataclasses import dataclass
from pathlib import Path
from typing import cast

from blizzard.foundation.roles import collaborator
from blizzard.runner.process.worker_scratch import IWorkerScratchDirs


def _directory(path: Path) -> bool:
    try:
        return stat.S_ISDIR(path.lstat().st_mode)
    except FileNotFoundError:
        return False


def _mkdir(path: Path) -> None:
    try:
        path.mkdir(mode=0o700)
        _sync_directory(path.parent)
    except FileExistsError:
        if not _directory(path):
            raise OSError(f"Scratch path is not a directory: {path}") from None


def _sync_directory(path: Path) -> None:
    fd = os.open(path, os.O_RDONLY | os.O_DIRECTORY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def _read(path: Path) -> dict[str, str] | None:
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd) as f:
            info = os.fstat(f.fileno())
            if not stat.S_ISREG(info.st_mode):
                return None
            value: object = json.load(f)
            if not isinstance(value, dict):
                return None
            value = cast(dict[str, str], value)
            return (
                value
                if (isinstance(value.get("id"), str) and isinstance(value.get("token"), str) and value["token"])
                else None
            )
    except (OSError, ValueError):
        return None


def _write(path: Path, value: object, *, exclusive: bool = False) -> None:
    fd, name = tempfile.mkstemp(prefix=".new-", dir=path.parent)
    try:
        with os.fdopen(fd, "w") as f:
            json.dump(value, f)
            f.flush()
            os.fsync(f.fileno())
        if exclusive:
            os.link(name, path)
        else:
            os.replace(name, path)
        _sync_directory(path.parent)
    finally:
        with contextlib.suppress(FileNotFoundError):
            os.unlink(name)


def _owned(path: Path, token: str) -> bool:
    return _directory(path) and _read(path / ".owner") == {"id": "directory", "token": token}


def _identity(path: Path) -> str:
    info = path.lstat()
    return f"{info.st_dev}:{info.st_ino}"


def _payload_owned(path: Path, record: dict[str, str]) -> bool:
    if not _directory(path):
        return False
    if "directory" in record:
        return _identity(path) == record["directory"]
    return _owned(path, record["token"])


def _publish_directory(path: Path, token: str) -> None:
    if os.path.lexists(path):
        if not _owned(path, token):
            raise OSError(f"Scratch directory ownership mismatch: {path}")
        return
    staged = Path(tempfile.mkdtemp(prefix=".stage-", dir=path.parent))
    try:
        _write(staged / ".owner", {"id": "directory", "token": token})
        # Never replace even an empty directory published by another actor.
        libc = ctypes.CDLL(None, use_errno=True)
        result = libc.renameat2(-100, os.fsencode(staged), -100, os.fsencode(path), 1)
        if result:
            error = ctypes.get_errno()
            if error != errno.EEXIST or not _owned(path, token):
                raise OSError(error, os.strerror(error), str(path))
        _sync_directory(path.parent)
    finally:
        if staged.exists():
            shutil.rmtree(staged)


def _component(identity: str, prefix: str, length: int) -> str:
    return prefix + identity.split("_", 1)[-1][-length:].lower()


def _valid_component(name: str, identity: str, prefix: str) -> bool:
    tail = identity.split("_", 1)[-1]
    return any(name == _component(identity, prefix, length) for length in range(4, len(tail) + 1))


@collaborator
@dataclass(frozen=True)
class WorkerScratchDirs:
    """Compact reservations under root; an empty root disables scratch.

    ``legacy_root`` is independent of root; an empty runner id uses the legacy adapter shape.
    """

    root: str
    runner_id: str = ""
    legacy_root: str = ""
    legacy_owned: Callable[[str], bool] = lambda _: False

    @contextlib.contextmanager
    def _locked(self) -> Generator[Path]:
        root = Path(self.root)
        root.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not _directory(root):
            raise OSError(f"Scratch root is not a directory: {root}")
        fd = os.open(root / ".lock", os.O_CREAT | os.O_RDWR | os.O_NOFOLLOW, 0o600)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX)
            yield root
        finally:
            os.close(fd)

    def _runner(self, root: Path, create: bool) -> tuple[Path, Path] | None:
        owners = root / ".owners"
        if create:
            _mkdir(owners)
        elif not _directory(owners):
            return None
        # Records are authoritative even if their payload has disappeared.
        for record in owners.glob("r-*.json"):
            if (
                (entry := _read(record))
                and entry["id"] == self.runner_id
                and _valid_component(record.name[:-5], self.runner_id, "r-")
            ):
                name = record.name[:-5]
                records = owners / name
                _publish_directory(records, entry["token"])
                return root / name, records
        if not create:
            return None
        tail = self.runner_id.split("_", 1)[-1]
        for length in range(4, len(tail) + 1):
            name = _component(self.runner_id, "r-", length)
            record, payload, records = owners / (name + ".json"), root / name, owners / name
            if os.path.lexists(record) or os.path.lexists(payload) or os.path.lexists(records):
                continue
            token = secrets.token_hex(32)
            _write(record, {"id": self.runner_id, "token": token}, exclusive=True)
            _publish_directory(records, token)
            return payload, records
        raise OSError("Scratch runner identity collision cannot be resolved")

    def _lease(self, root: Path, lease_id: str, create: bool) -> tuple[Path, Path] | None:
        runner = self._runner(root, create)
        if runner is None:
            return None
        payload, records = runner
        if os.path.lexists(payload) and not _directory(payload):
            raise OSError(f"Scratch runner path is not a directory: {payload}")
        for record in records.glob("l-*.json"):
            if (
                (entry := _read(record))
                and entry["id"] == lease_id
                and _valid_component(record.name[:-5], lease_id, "l-")
            ):
                return payload / record.name[:-5], record
        if not create:
            return None
        tail = lease_id.split("_", 1)[-1]
        for length in range(4, len(tail) + 1):
            name = _component(lease_id, "l-", length)
            record, target = records / (name + ".json"), payload / name
            if os.path.lexists(record) or os.path.lexists(target):
                continue
            _write(record, {"id": lease_id, "token": secrets.token_hex(32)}, exclusive=True)
            return target, record
        raise OSError("Scratch lease identity collision cannot be resolved")

    def _legacy(self, lease_id: str) -> Path | None:
        if not self.legacy_root or not self.legacy_owned(lease_id):
            return None
        root = Path(self.legacy_root)
        if not _directory(root):
            return None
        runner = root / self.runner_id
        if _directory(runner) and _directory(runner / lease_id):
            return runner / lease_id
        flat = root / lease_id
        return flat if _directory(flat) else None

    def path(self, lease_id: str) -> str:
        if not self.root:
            return ""
        if not self.runner_id:
            return os.path.join(self.root, lease_id)
        with self._locked() as root:
            legacy = self._legacy(lease_id)
            if legacy is not None:
                return str(legacy)
            allocation = self._lease(root, lease_id, True)
            assert allocation is not None
            return str(allocation[0])

    def ensure(self, lease_id: str) -> str:
        if not self.root:
            return ""
        if not self.runner_id:
            path = self.path(lease_id)
            os.makedirs(path, mode=0o700, exist_ok=True)
            os.chmod(path, 0o700)
            return path
        with self._locked() as root:
            legacy = self._legacy(lease_id)
            if legacy is not None:
                return str(legacy)
            allocation = self._lease(root, lease_id, True)
            assert allocation is not None
            path = allocation[0]
            runner_record = _read(root / ".owners" / (path.parent.name + ".json"))
            lease_record = _read(allocation[1])
            assert runner_record is not None and lease_record is not None
            if "deleting" in lease_record:
                raise OSError(f"Scratch deletion in progress: {path}")
            _publish_directory(path.parent, runner_record["token"])
            if "directory" in lease_record and not os.path.lexists(path):
                del lease_record["directory"]
                lease_record["token"] = secrets.token_hex(32)
                _write(allocation[1], lease_record)
            if os.path.lexists(path) and "directory" in lease_record:
                if not _payload_owned(path, lease_record):
                    raise OSError(f"Scratch directory ownership mismatch: {path}")
            else:
                _publish_directory(path, lease_record["token"])
            if "directory" not in lease_record:
                lease_record["directory"] = _identity(path)
                _write(allocation[1], lease_record)
            return str(path)

    def _remove(self, root: Path, lease_id: str, *, legacy: bool = True) -> bool:
        old_path = self._legacy(lease_id) if legacy else None
        if old_path is not None:
            shutil.rmtree(old_path)
            return True
        allocation = self._lease(root, lease_id, False)
        if allocation is None:
            return False
        path, record = allocation
        runner_record = _read(root / ".owners" / (path.parent.name + ".json"))
        lease_record = _read(record)
        if runner_record is None or lease_record is None:
            return False
        if os.path.lexists(path.parent) and not _owned(path.parent, runner_record["token"]):
            return False
        tombstone = record.parent / (".deleting-" + lease_record["token"])
        if "deleting" not in lease_record and os.path.lexists(path):
            if not _payload_owned(path, lease_record) or os.path.lexists(tombstone):
                return False
            info = path.lstat()
            lease_record["deleting"] = f"{info.st_dev}:{info.st_ino}"
            _write(record, lease_record)
        if "deleting" in lease_record:

            def matches(candidate: Path) -> bool:
                if not _directory(candidate):
                    return False
                info = candidate.lstat()
                return lease_record["deleting"] == f"{info.st_dev}:{info.st_ino}"

            if os.path.lexists(tombstone):
                if not matches(tombstone):
                    return False
            elif "moved" not in lease_record and os.path.lexists(path):
                if not matches(path):
                    return False
                os.rename(path, tombstone)
                _sync_directory(path.parent)
                _sync_directory(tombstone.parent)
            if os.path.lexists(tombstone):
                if "moved" not in lease_record:
                    lease_record["moved"] = "yes"
                    _write(record, lease_record)
                shutil.rmtree(tombstone)
                _sync_directory(tombstone.parent)
        record.unlink()
        _sync_directory(record.parent)
        return True

    def remove(self, lease_id: str) -> None:
        if not self.root:
            return
        with contextlib.suppress(OSError):
            if not self.runner_id:
                shutil.rmtree(self.path(lease_id))
            else:
                with self._locked() as root:
                    self._remove(root, lease_id)

    def sweep_orphans(self, active_lease_ids: Iterable[str], *, strict: bool = False) -> int:
        if not self.root:
            return 0
        if not _directory(Path(self.root)):
            if strict and os.path.lexists(self.root):
                raise OSError(f"Scratch root is not a directory: {self.root}")
            return 0
        active = set(active_lease_ids)
        if not self.runner_id:
            removed = 0
            for entry in os.scandir(self.root):
                if entry.name not in active and entry.is_dir(follow_symlinks=False):
                    with contextlib.suppress(OSError):
                        shutil.rmtree(entry.path)
                        removed += 1
            return removed
        removed = 0
        with self._locked() as root:
            runner = self._runner(root, False)
            if runner is None or not _directory(runner[1]):
                return 0
            for record in runner[1].glob("l-*.json"):
                entry = _read(record)
                lease_id = entry["id"] if entry else None
                if (
                    lease_id is not None
                    and lease_id not in active
                    and _valid_component(record.name[:-5], lease_id, "l-")
                ):
                    try:
                        if self._remove(root, lease_id, legacy=False):
                            removed += 1
                        elif strict:
                            raise OSError(f"Cannot remove scratch allocation for {lease_id}")
                    except OSError:
                        if strict:
                            raise
        return removed

    def sweep_flat_entries(self, removable: Callable[[str], bool], *, strict: bool = False) -> int:
        root = Path(self.legacy_root or self.root)
        if not self.root or not _directory(root):
            return 0
        removed = 0
        for entry in os.scandir(root):
            if not entry.name.startswith("rn_") and entry.is_dir(follow_symlinks=False) and removable(entry.name):
                try:
                    shutil.rmtree(entry.path)
                    removed += 1
                except OSError:
                    if strict:
                        raise
        return removed

    def sweep_legacy(self, removable: Callable[[str], bool], *, strict: bool = False) -> int:
        """Remove legacy payloads only when the caller proves closure and ownership."""
        if not self.legacy_root or not _directory(Path(self.legacy_root)):
            return 0
        removed = self.sweep_flat_entries(removable, strict=strict)
        runner = Path(self.legacy_root) / self.runner_id
        if _directory(runner):
            for entry in os.scandir(runner):
                if entry.is_dir(follow_symlinks=False) and removable(entry.name):
                    try:
                        shutil.rmtree(entry.path)
                        removed += 1
                    except OSError:
                        if strict:
                            raise
        return removed


_worker_scratch_dirs_conforms: type[IWorkerScratchDirs] = WorkerScratchDirs
