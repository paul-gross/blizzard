"""The identity-link repository seam — read/write Protocols (issue #91,
``bzh:repository-split``).

No login mechanism exists yet in this phase, so nothing writes an :class:`Identity`
row through this seam — the write Protocol is landed now (rather than in #92) so the
schema and repository shape are stable for the linking rule #92 adds on top, per this
issue's own design note ("parse-and-carry now so the schema is stable").
"""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import Identity


class IReadIdentityRepository(Protocol):
    """Read-only identity-link lookups."""

    def get(self, provider_name: str, subject: str) -> Identity | None: ...
    def list_for_user(self, user_id: str) -> list[Identity]: ...


class IWriteIdentityRepository(IReadIdentityRepository, Protocol):
    """Read-write identity-link access — only the domain layer depends on this variant."""

    def link(self, identity: Identity) -> None: ...
