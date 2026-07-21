"""The identity-link repository seam — read/write Protocols (issue #91,
``bzh:repository-split``).

The write Protocol landed in #91 ahead of a login mechanism so the schema and
repository shape were stable for #92's linking rule; #92 is the first caller of
:meth:`IWriteIdentityRepository.link` (and its own
:meth:`~IWriteIdentityRepository.update_handle`, the handle-refresh half of "subject
mapping wins" — a provider rename never re-mints a user), plus
:meth:`IReadIdentityRepository.distinct_provider_names`, the boot-time
provider-immutability check's read (``hub/app.py``'s ``build_hosted_app``).
"""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.auth.models import Identity


class IReadIdentityRepository(Protocol):
    """Read-only identity-link lookups."""

    def get(self, provider_name: str, subject: str) -> Identity | None: ...
    def list_for_user(self, user_id: str) -> list[Identity]: ...

    def distinct_provider_names(self) -> set[str]:
        """Every ``provider_name`` any stored identity references — the boot-time
        immutability check's read: a name here absent from ``[[auth.oauth.provider]]``
        means a rename would silently orphan those identities (issue #92)."""
        ...


class IWriteIdentityRepository(IReadIdentityRepository, Protocol):
    """Read-write identity-link access — only the domain layer depends on this variant."""

    def link(self, identity: Identity) -> None: ...

    def update_handle(self, provider_name: str, subject: str, *, handle: str) -> None:
        """Refresh a linked identity's stored ``handle`` in place (issue #92) — a
        provider-side handle rename never re-mints a user; "subject mapping wins on
        every later login" (the epic's own phrasing) applies to the handle too."""
        ...
