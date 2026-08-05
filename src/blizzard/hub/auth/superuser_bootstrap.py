"""The superuser-bootstrap repository seam — read/write Protocols (issue #94,
``bzh:repository-split``).

Backs the ``superuser_bootstrap`` singleton row: a domain-only seam, never reached
from the edge."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import SuperuserBootstrap


class IReadSuperuserBootstrapRepository(Protocol):
    """Read-only lookup of the singleton bootstrap row."""

    def get(self) -> SuperuserBootstrap | None: ...


class IWriteSuperuserBootstrapRepository(IReadSuperuserBootstrapRepository, Protocol):
    """Read-write bootstrap access — only the domain layer depends on this variant."""

    def upsert(self, bootstrap: SuperuserBootstrap) -> None:
        """Replace the singleton row with ``bootstrap`` (there is ever at most one)."""
        ...

    def clear(self) -> None:
        """Delete the singleton row outright — ``auth.superuser`` was unset."""
        ...
