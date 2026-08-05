"""The single-use ``state`` repository seam (issue #92, decision D5,
``bzh:repository-split``).

One :class:`~blizzard.hub.auth.models.AuthStateEntry` is written per redirect, and
:meth:`IWriteAuthStateRepository.consume` reads-and-deletes it in one call, so a
replayed ``state`` value can never resolve twice."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import AuthStateEntry


class IReadAuthStateRepository(Protocol):
    """Read-only ``state`` lookups."""

    def get(self, state: str) -> AuthStateEntry | None: ...


class IWriteAuthStateRepository(IReadAuthStateRepository, Protocol):
    """Read-write ``state`` access — only the domain layer depends on this variant."""

    def create(self, entry: AuthStateEntry) -> None: ...

    def consume(self, state: str) -> AuthStateEntry | None:
        """Read-and-delete ``state`` in one call — single-use: a second call with the
        same value resolves to ``None``, exactly as an expired or forged one does."""
        ...
