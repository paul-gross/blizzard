"""Scope domain model — an operator-authored slug the hub stores and hands back, never
resolves.

Minted the moment its slug is first named, by ``scope create`` or a routine naming an
unseen default (:class:`ScopeRegistry.ensure`). Retire/enable is a reversible,
append-only, newest-fact-wins brake, exactly like a graph's (``bzh:facts-not-status``)."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.brake import ANY_STATE, ENABLED_ONLY, BrakeState, BrakeVerb

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")


class ScopeSlugError(ValueError):
    """A scope slug is empty or outside ``[a-z0-9-]+`` — names the offending value."""


@domain_model
@dataclass(frozen=True)
class ScopeSlug:
    """A validated scope slug — the only way to obtain one is :meth:`parse`."""

    value: str

    @classmethod
    def parse(cls, raw: str) -> ScopeSlug:
        if not raw or not _SLUG_PATTERN.match(raw):
            raise ScopeSlugError(f"scope slug must match [a-z0-9-]+, got {raw!r}")
        return cls(raw)


class ScopeVerb(StrEnum):
    """Every act that names an existing scope."""

    CREATE = "create"
    NAME_AS_DEFAULT = "name_as_default"
    EDIT_DESCRIPTION = "edit_description"
    RETIRE = "retire"
    ENABLE = "enable"
    LINK = "link"
    UNLINK = "unlink"
    RUN_AGAINST = "run_against"
    READ = "read"


@domain_model
@dataclass(frozen=True)
class Scope:
    slug: str
    description: str
    created_at: datetime

    #: Which verbs are legal from which brake state; retiring withdraws a scope from selection only.
    LEGAL_FROM: ClassVar[Mapping[ScopeVerb, frozenset[BrakeState]]] = {
        ScopeVerb.CREATE: ANY_STATE,
        ScopeVerb.NAME_AS_DEFAULT: ANY_STATE,
        ScopeVerb.EDIT_DESCRIPTION: ANY_STATE,
        ScopeVerb.RETIRE: ANY_STATE,
        ScopeVerb.ENABLE: ANY_STATE,
        ScopeVerb.LINK: ANY_STATE,
        ScopeVerb.UNLINK: ANY_STATE,
        ScopeVerb.RUN_AGAINST: ENABLED_ONLY,
        ScopeVerb.READ: ANY_STATE,
    }

    @staticmethod
    def allows(verb: ScopeVerb, *, retired: bool) -> bool:
        """Whether ``verb`` is legal on a scope whose newest lifecycle fact reads
        ``retired``."""
        return BrakeState.of(retired=retired) in Scope.LEGAL_FROM[verb]


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadScopeRepository(Protocol):
    """Read-only scope access. Controllers at the edges depend on this variant."""

    def get(self, slug: str) -> Scope | None: ...

    def list_all(self) -> list[Scope]: ...

    def is_retired(self, slug: str) -> bool:
        """Whether ``slug``'s newest lifecycle fact reads retired.

        ``False`` for a slug with no lifecycle fact at all — every freshly minted scope
        starts enabled."""
        ...

    def retired_slugs(self) -> set[str]:
        """Every slug whose newest lifecycle fact reads retired — the bulk
        counterpart to :meth:`is_retired`, mirroring
        ``IReadGraphRepository.retired_graph_ids``."""
        ...


class IWriteScopeRepository(IReadScopeRepository, Protocol):
    """Read-write scope access. Only the domain layer depends on this variant."""

    def ensure(self, slug: str, *, description: str, at: datetime) -> Scope:
        """Mint ``slug`` if unseen; otherwise read back the existing row unchanged
        — first-write-wins CAS, never overwriting a stored description."""
        ...

    def edit_description(self, slug: str, *, description: str) -> Scope:
        """Change an existing scope's description in place — the row itself is a
        mutable entity, not a fact."""
        ...

    def record_lifecycle(self, slug: str, *, retired: bool, at: datetime, by: str) -> None:
        """Append a ``scope.retired``/``scope.enabled`` fact — newest-fact-wins.

        Never touches the ``scopes`` row itself."""
        ...


class ScopeRegistry:
    """Mint-on-name and edit-description over the scope repository."""

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
    """Set or clear a scope's retired brake without touching its row."""

    def __init__(self, *, scopes: IWriteScopeRepository, clock: IClock) -> None:
        self._scopes = scopes
        self._clock = clock

    def retire(self, scope: Scope, *, by: str) -> None:
        """Append ``scope.retired``. Idempotent: retiring an already-retired scope just
        appends another ``retired=True`` fact, a harmless no-op via newest-fact-wins."""
        self._record(scope, BrakeVerb.RETIRE, by=by)

    def enable(self, scope: Scope, *, by: str) -> None:
        """Append ``scope.enabled``. Idempotent on an already-enabled scope."""
        self._record(scope, BrakeVerb.ENABLE, by=by)

    def _record(self, scope: Scope, verb: BrakeVerb, *, by: str) -> None:
        self._scopes.record_lifecycle(scope.slug, retired=verb.records_retired, at=self._clock.now(), by=by)
