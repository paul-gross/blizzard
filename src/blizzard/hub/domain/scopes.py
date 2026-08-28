"""Scope domain model — an operator-authored slug the hub stores and hands back, never
resolves (issue #389).

A scope is minted the moment its slug is first named — by an explicit ``scope create``
or by a routine naming an unseen default scope — so :class:`ScopeRegistry.ensure` is the
one mint path both namers share (D4). Retire/enable is a reversible brake, append-only
and newest-fact-wins, exactly like a graph's (``bzh:facts-not-status``)."""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


class ScopeSlugError(ValueError):
    """A scope slug is empty or outside ``[a-z0-9-]+`` — names the offending value."""


@dataclass(frozen=True)
class ScopeSlug:
    """A validated scope slug — the only way to obtain one is :meth:`parse`."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> ScopeSlug:
        if not raw or not _SLUG_PATTERN.match(raw):
            raise ScopeSlugError(f"scope slug must match [a-z0-9-]+, got {raw!r}")
        return cls(raw)


@dataclass(frozen=True)
class Scope:
    slug: str
    description: str
    created_at: datetime


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadScopeRepository(Protocol):
    """Read-only scope access. Controllers at the edges depend on this variant."""

    def get(self, slug: str) -> Scope | None: ...

    def list_all(self) -> list[Scope]: ...

    def is_retired(self, slug: str) -> bool:
        """Whether ``slug``'s newest lifecycle fact reads retired (issue #389).

        ``False`` for a slug with no lifecycle fact at all — every freshly minted scope
        starts enabled."""
        ...


class IWriteScopeRepository(IReadScopeRepository, Protocol):
    """Read-write scope access. Only the domain layer depends on this variant."""

    def ensure(self, slug: str, *, description: str, at: datetime) -> Scope:
        """Mint ``slug`` if unseen; otherwise read back the existing row unchanged
        (D4, D5) — first-write-wins CAS, never overwriting a stored description."""
        ...

    def edit_description(self, slug: str, *, description: str) -> Scope:
        """Change an existing scope's description in place (D3) — the row itself is a
        mutable entity, not a fact."""
        ...

    def record_lifecycle(self, slug: str, *, retired: bool, at: datetime, by: str) -> None:
        """Append a ``scope.retired``/``scope.enabled`` fact — newest-fact-wins (D3).

        Never touches the ``scopes`` row itself."""
        ...


class ScopeRegistry:
    """Mint-on-name and edit-description over the scope repository (D4, issue #389)."""

    def __init__(self, *, scopes: IWriteScopeRepository, clock: IClock) -> None:
        self._scopes = scopes
        self._clock = clock

    def ensure(self, slug: ScopeSlug, *, description: str = "") -> Scope:
        """Mint ``slug`` if unseen, else return the existing scope unchanged — the one
        mint path a ``scope create`` and a routine naming a default scope both call."""
        return self._scopes.ensure(slug.value, description=description, at=self._clock.now())

    def edit(self, scope: Scope, *, description: str) -> Scope:
        """Change ``scope``'s description in place — never touches its slug."""
        return self._scopes.edit_description(scope.slug, description=description)


class ScopeLifecycle:
    """Set or clear a scope's retired brake without touching its row (D3, issue #389)."""

    def __init__(self, *, scopes: IWriteScopeRepository, clock: IClock) -> None:
        self._scopes = scopes
        self._clock = clock

    def retire(self, scope: Scope, *, by: str) -> None:
        """Append ``scope.retired``. Idempotent: retiring an already-retired scope just
        appends another ``retired=True`` fact, a harmless no-op via newest-fact-wins."""
        self._scopes.record_lifecycle(scope.slug, retired=True, at=self._clock.now(), by=by)

    def enable(self, scope: Scope, *, by: str) -> None:
        """Append ``scope.enabled``. Idempotent on an already-enabled scope."""
        self._scopes.record_lifecycle(scope.slug, retired=False, at=self._clock.now(), by=by)
