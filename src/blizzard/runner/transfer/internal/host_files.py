"""The host-disk adapter of :class:`~blizzard.runner.transfer.host_files.IHostFiles`."""

from __future__ import annotations

import contextlib
import shutil
import sqlite3
import tempfile
from collections.abc import Callable, Generator, Iterable
from contextlib import AbstractContextManager
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import collaborator
from blizzard.runner.transfer.host_files import IHostFiles, NotAStoreFile

__all__ = ["HostFiles", "backup_sqlite"]

#: The name prefix of an ingest's store-copy directory.
_COPY_PREFIX = "ingest-copy-"


class _SetAside(Protocol):
    @property
    def moved(self) -> tuple[Path, ...]: ...


class _SetsAside(Protocol):
    def __call__(self, database: Path, *, now: datetime) -> _SetAside: ...


class _Layout(Protocol):
    @property
    def socket_path(self) -> Path: ...

    @property
    def legacy_socket_path(self) -> Path: ...


class _HoldsHost[L: _Layout](Protocol):
    def __call__(
        self,
        layout: L,
        sockets: Iterable[Path],
        probe: Callable[[Path], str | None],
        refusal: Callable[[Exception], Exception],
    ) -> AbstractContextManager[None]: ...


def backup_sqlite(database: Path, copy: Path) -> None:
    """Copy the SQLite store ``database`` into ``copy`` through SQLite's backup API, read over a read-only connection:
    the copy holds every committed transaction, its write-ahead log's among them, and neither the database nor its log
    is written — a read-only connection runs no checkpoint."""
    with (
        contextlib.closing(sqlite3.connect(f"{database.resolve().as_uri()}?mode=ro", uri=True)) as source,
        contextlib.closing(sqlite3.connect(copy)) as destination,
    ):
        source.backup(destination)


@collaborator
@dataclass(frozen=True)
class HostFiles[L: _Layout]:
    """An ingest's file operations on the host's disk. The store-file lookup, store URL, set-aside and host hold it
    shares with the offline store verbs are their edge owner's, injected."""

    #: The database file a store URL names, raising ``not_a_store_file`` for a store that is not a SQLite file.
    locate_store: Callable[[str], Path]
    not_a_store_file: type[Exception]
    url_of: Callable[[Path], str]
    set_aside_store: _SetsAside
    layout_of: Callable[[Path], L]
    hold_host: _HoldsHost[L]

    def store_file(self, db_url: str) -> Path:
        try:
            return self.locate_store(db_url)
        except self.not_a_store_file as exc:
            raise NotAStoreFile(str(exc)) from exc

    def store_url(self, database: Path) -> str:
        return self.url_of(database)

    def resolved(self, path: Path) -> Path:
        return path.resolve()

    def exists(self, path: Path) -> bool:
        return path.exists()

    @contextlib.contextmanager
    def copy_of(self, store: Path, beside: Path) -> Generator[Path, None, None]:
        # The caller holds the host's lock, so a copy left here is one a killed run never removed.
        for stale in beside.glob(f"{_COPY_PREFIX}*"):
            shutil.rmtree(stale, ignore_errors=True)
        directory = Path(tempfile.mkdtemp(prefix=_COPY_PREFIX, dir=beside))
        try:
            copy = directory / store.name
            backup_sqlite(store, copy)
            yield copy
        finally:
            shutil.rmtree(directory, ignore_errors=True)

    def copy_path(self, src: Path, dst: Path) -> None:
        if src.is_dir():
            shutil.copytree(src, dst, dirs_exist_ok=True)
        elif src.exists():
            dst.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src, dst)

    def make_directory(self, path: Path) -> None:
        path.mkdir(parents=True, exist_ok=True)

    def set_aside(self, store: Path, *, now: datetime) -> tuple[Path, ...]:
        return self.set_aside_store(store, now=now).moved

    @contextlib.contextmanager
    def hold(
        self, root: Path, probe: Callable[[Path], str | None], refusal: Callable[[str], Exception]
    ) -> Generator[None, None, None]:
        layout = self.layout_of(root)
        with self.hold_host(
            layout, (layout.socket_path, layout.legacy_socket_path), probe, lambda held: refusal(str(held))
        ):
            yield


_host_files_conforms: type[IHostFiles] = HostFiles
