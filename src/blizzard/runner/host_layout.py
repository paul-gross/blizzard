"""A host directory's layout and its lock.

The layout is the directory's filenames and its graduation state; the lock is the one owner a host directory has.
Every write to ``blizzard-host.toml`` is :mod:`blizzard.runner.host_file`'s."""

from __future__ import annotations

import fcntl
import os
from collections.abc import Callable, Generator, Iterable
from contextlib import contextmanager
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from types import TracebackType

from blizzard.foundation.roles import collaborator, domain_model

HOST_CONFIG_FILENAME = "blizzard-host.toml"
DATA_DIRNAME = "data"
HOST_STORE_FILENAME = "host.db"
HOST_LOCK_FILENAME = "host.lock"
#: The host's local API socket, in the host directory itself.
HOST_SOCKET_FILENAME = "host.sock"

#: Where a host keeps each runner's own files, by runner name.
RUNNERS_DIRNAME = "runners"

#: A runner's runtime root keeps three directories across restarts: each worker's stdout, each elicitation's output
#: (both lease-keyed files), and the per-lease scratch directories, under a subtree per runner id.
WORKER_STDOUT_DIRNAME = "worker-stdout"
ELICITATION_OUTPUT_DIRNAME = "elicitation-output"
WORKER_SCRATCH_DIRNAME = "worker-tmp"

#: What a directory that has not graduated still holds.
LEGACY_CONFIG_FILENAME = "blizzard-runner.toml"
LEGACY_STORE_FILENAME = "runner.db"
LEGACY_SOCKET_FILENAME = "runner.sock"

#: The verb that creates or adopts a host directory's store; it takes the directory as ``--dir``.
MIGRATE_COMMAND = "blizzard runner migrate"

_OWNER_ONLY = 0o600
_PID_BYTES = 32


def migrate_remedy(root: Path) -> str:
    """The runnable command that migrates the host directory at ``root``."""
    return f"{MIGRATE_COMMAND} --dir {root}"


class Graduation(StrEnum):
    """Whether a directory is a host: it holds ``blizzard-host.toml`` (``GRADUATED``), holds only the
    pre-host ``blizzard-runner.toml`` (``UNGRADUATED``), or holds neither (``UNINITIALISED``)."""

    GRADUATED = "graduated"
    UNGRADUATED = "ungraduated"
    UNINITIALISED = "uninitialised"


@domain_model
@dataclass(frozen=True)
class HostLayout:
    """The files a host directory holds, named from its root."""

    root: Path

    @property
    def config_path(self) -> Path:
        return self.root / HOST_CONFIG_FILENAME

    @property
    def data_dir(self) -> Path:
        return self.root / DATA_DIRNAME

    @property
    def store_path(self) -> Path:
        return self.data_dir / HOST_STORE_FILENAME

    @property
    def default_db_url(self) -> str:
        return f"sqlite:///{self.store_path.resolve()}"

    @property
    def lock_path(self) -> Path:
        return self.data_dir / HOST_LOCK_FILENAME

    @property
    def socket_path(self) -> Path:
        return self.root / HOST_SOCKET_FILENAME

    @property
    def legacy_config_path(self) -> Path:
        return self.root / LEGACY_CONFIG_FILENAME

    @property
    def legacy_store_path(self) -> Path:
        return self.data_dir / LEGACY_STORE_FILENAME

    @property
    def legacy_socket_path(self) -> Path:
        return self.root / LEGACY_SOCKET_FILENAME

    def graduation(self) -> Graduation:
        if self.config_path.exists():
            return Graduation.GRADUATED
        if self.legacy_config_path.exists():
            return Graduation.UNGRADUATED
        return Graduation.UNINITIALISED


class HostHeld(RuntimeError):
    """The host directory is in use: another process holds its lock, or a daemon answers on one of its sockets. The
    message names which; ``pid`` is the lock holder's, when it recorded one."""

    def __init__(self, cause: str, pid: int | None = None) -> None:
        self.pid = pid
        super().__init__(cause)

    @classmethod
    def locked(cls, lock_path: Path, pid: int | None) -> HostHeld:
        holder = f"pid {pid}" if pid is not None else "another process"
        return cls(f"the host directory is held by {holder} ({lock_path})", pid)


@collaborator
@dataclass
class HostLock:
    """The exclusive, non-blocking ``flock`` on ``data/host.lock``, held until :meth:`release` — or the
    process's end, which the kernel releases it at. The file names the holder's pid."""

    path: Path
    fd: int

    @classmethod
    def acquire(cls, layout: HostLayout) -> HostLock:
        """Take the lock, or raise :class:`HostHeld` naming the pid its holder recorded."""
        layout.data_dir.mkdir(parents=True, exist_ok=True)
        fd = os.open(layout.lock_path, os.O_RDWR | os.O_CREAT, _OWNER_ONLY)
        try:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            recorded = os.pread(fd, _PID_BYTES, 0).decode(errors="replace").strip()
            os.close(fd)
            raise HostHeld.locked(layout.lock_path, int(recorded) if recorded.isdigit() else None) from None
        os.ftruncate(fd, 0)
        os.pwrite(fd, str(os.getpid()).encode(), 0)
        return cls(layout.lock_path, fd)

    def release(self) -> None:
        if self.fd >= 0:
            os.close(self.fd)
            self.fd = -1

    def __enter__(self) -> HostLock:
        return self

    def __exit__(
        self, exc_type: type[BaseException] | None, exc: BaseException | None, tb: TracebackType | None
    ) -> None:
        self.release()


@contextmanager
def hold_host(
    layout: HostLayout,
    sockets: Iterable[Path],
    probe: Callable[[Path], str | None],
    refusal: Callable[[HostHeld], Exception] = lambda held: held,
) -> Generator[None, None, None]:
    """``layout``'s directory held for a write: its lock taken, and no daemon answering on any of ``sockets``, the
    ones the caller names — ``probe`` answers what holds a socket, else ``None``. A refusal names its cause as a
    :class:`HostHeld` and raises what ``refusal`` makes of it; only the hold's own refusals are mapped, never an
    error the body raises. The lock is released on exit."""
    try:
        lock = HostLock.acquire(layout)
    except HostHeld as exc:
        raise refusal(exc) from exc
    with lock:
        for socket in sockets:
            if (held := probe(socket)) is not None:
                raise refusal(HostHeld(held))
        yield
