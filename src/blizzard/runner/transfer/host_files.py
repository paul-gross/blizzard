"""What an ingest does to files on the host: the copy of a source store, the copies of configured paths, the set-aside
of an emptied store, and the hold on a host directory."""

from __future__ import annotations

from collections.abc import Callable
from contextlib import AbstractContextManager
from datetime import datetime
from pathlib import Path
from typing import Protocol

__all__ = ["IHostFiles", "NotAStoreFile"]


class NotAStoreFile(Exception):
    """A store URL that names no file, so there is none to move aside or copy."""


class IHostFiles(Protocol):
    def store_file(self, db_url: str) -> Path:
        """The database file the store URL ``db_url`` names. Raises :class:`NotAStoreFile` for a store that is not a
        SQLite file."""
        ...

    def store_url(self, database: Path) -> str:
        """The store URL that opens the SQLite file ``database``."""
        ...

    def copy_of(self, store: Path, beside: Path) -> AbstractContextManager[Path]:
        """Stale ingest copies under ``beside`` removed, then a fresh directory made there holding a backup of
        ``store``; yields the copy's file. The directory is removed on leaving, an error included."""
        ...

    def copy_tree(self, src: Path, dst: Path) -> None:
        """The directory ``src`` copied into ``dst``, merging with what ``dst`` holds."""
        ...

    def copy_file(self, src: Path, dst: Path) -> None:
        """The file ``src`` copied to ``dst``, ``dst``'s parent made first."""
        ...

    def set_aside(self, store: Path, *, now: datetime) -> tuple[Path, ...]:
        """The files of ``store`` moved aside, never deleted; the files moved."""
        ...

    def hold(
        self, root: Path, probe: Callable[[Path], str | None], refusal: Callable[[str], Exception]
    ) -> AbstractContextManager[None]:
        """The host directory ``root`` held: its lock taken, and nothing answering on its sockets — ``probe`` tells
        what holds a socket. A refusal raises what ``refusal`` makes of its cause's message."""
        ...
