"""The single-use ``state`` repository seam (issue #92, decision D5,
``bzh:repository-split``).

``authorize`` writes one :class:`~blizzard.hub.auth.models.AuthStateEntry` per redirect;
``callback`` reads-and-deletes it in one call (:meth:`IWriteAuthStateRepository.consume`)
so a replayed ``state`` query parameter can never resolve twice. The same table (and this
same seam) is reused by #95's hub-as-IdP authorize endpoint — nothing here is
provider-login-specific.
"""

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
