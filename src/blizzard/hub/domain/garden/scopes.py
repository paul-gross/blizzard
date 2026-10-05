"""Scope domain model — an operator-authored slug the hub stores and hands back, never
resolves.

Minted the moment its slug is first named, by ``scope create`` or a routine naming an
unseen default (:class:`ScopeRegistry.resolve`). A scope is a configured record: every write
moves its revision and commits one change row (``bzh:configured-record``). Retire/enable is
a reversible, append-only, newest-fact-wins brake (``bzh:facts-not-status``) whose repeat
writes nothing."""

from __future__ import annotations

import re
from collections.abc import Mapping
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.changes import (
    FIELDED_RECORD_TRANSITIONS,
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
    RecordState,
    Verdict,
)
from blizzard.hub.domain.config.work_sources import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.garden.brake import ANY_STATE, ENABLED_ONLY, BrakeState, BrakeVerb
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

_SLUG_PATTERN = re.compile(r"^[a-z0-9-]+$")
_DESCRIPTION = "description"


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
class ScopeEdit:
    """A sparse scope edit: :data:`UNSET` leaves the description; ``None`` is refused."""

    description: str | None | UnsetType = UNSET


@domain_model
@dataclass(frozen=True)
class Scope:
    """A stored scope — a configured record keyed by its slug (``bzh:configured-record``).
    ``retired`` derives from the newest lifecycle fact.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it,
    or ``None`` when the verb changes nothing and so writes nothing."""

    slug: str
    description: str
    created_at: datetime
    revision: int = 1
    retired: bool = False

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

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

    @classmethod
    def new(cls, slug: ScopeSlug, description: str, ctx: ChangeContext, *, at: datetime) -> tuple[Scope, ConfigChange]:
        """A fresh scope at revision 1, with its ``create`` change."""
        record = cls(slug=slug.value, description=description, created_at=at)
        created = (FieldChange(_DESCRIPTION, None, description),)
        return record, ConfigChange.of(ctx, RecordKind.SCOPE, record.slug, 1, ChangeOp.CREATE, created, at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("scope", self.slug, current=self.revision)

    def edit(
        self, edit: ScopeEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Scope, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even
        for an edit that changes nothing."""
        self.require_revision(if_match)
        if edit.description is UNSET:
            return None
        if edit.description is None:
            raise ConfigFieldError(_DESCRIPTION, "must not be null")
        if edit.description == self.description:
            return None
        edited = replace(self, description=edit.description, revision=self.revision + 1)
        changes = (FieldChange(_DESCRIPTION, self.description, edit.description),)
        return edited, ConfigChange.of(ctx, RecordKind.SCOPE, self.slug, edited.revision, ChangeOp.EDIT, changes, at)

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Scope, ConfigChange] | None:
        """Retire or enable; ``None`` when the scope already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        if self.TRANSITIONS[RecordState.of(self.retired)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = (FieldChange(RETIRED_FIELD, self.retired, retired),)
        return moved, ConfigChange.of(ctx, RecordKind.SCOPE, self.slug, moved.revision, op, flip, at)


#: A scope mint the caller commits in its own transaction: the record and its ``create`` change.
ScopeMint = tuple[Scope, ConfigChange]


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
    """Read-write scope access. Only the domain layer depends on this variant; each write
    commits its ``change`` in the same transaction as the record write."""

    def ensure(self, record: Scope, *, change: ConfigChange) -> Scope:
        """Insert ``record`` with its ``create`` change if its slug is unseen; otherwise read
        back the stored scope unchanged and write nothing — first-write-wins, never
        overwriting a stored description."""
        ...

    def update(self, record: Scope, *, from_revision: int, change: ConfigChange) -> Scope:
        """Compare-and-set ``from_revision``, writing ``record``'s description and revision;
        :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(
        self, record: Scope, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Scope:
        """Append the ``scope.retired``/``scope.enabled`` fact and move the revision to
        ``record.revision``, as one compare-and-set on ``from_revision``."""
        ...


class ScopeRegistry:
    """Mint-on-name and edit over the scope repository."""

    def __init__(self, *, scopes: IWriteScopeRepository, clock: IClock) -> None:
        self._scopes = scopes
        self._clock = clock

    def ensure(self, slug: ScopeSlug, ctx: ChangeContext, *, description: str = "") -> Scope:
        """Mint ``slug`` if unseen, else return the existing scope unchanged — the one
        mint path ``scope create`` takes."""
        existing, mint = self.resolve(slug, ctx, description=description)
        if mint is None:
            return existing
        record, change = mint
        return self._scopes.ensure(record, change=change)

    def resolve(self, slug: ScopeSlug, ctx: ChangeContext, *, description: str = "") -> tuple[Scope, ScopeMint | None]:
        """The scope ``slug`` names and, when it is unseen, the mint the caller commits in its
        own transaction — how a routine naming an unseen default scope mints it."""
        existing = self._scopes.get(slug.value)
        if existing is not None:
            return existing, None
        mint = Scope.new(slug, description, ctx, at=self._clock.now())
        return mint[0], mint

    def edit(self, scope: Scope, edit: ScopeEdit, ctx: ChangeContext, *, if_match: int | None = None) -> Scope:
        """Apply a sparse edit — never touches the slug. An edit that changes nothing writes nothing."""
        decided = scope.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return scope
        edited, change = decided
        return self._scopes.update(edited, from_revision=scope.revision, change=change)


class ScopeLifecycle:
    """Set or clear a scope's retired brake without touching its row's fields."""

    def __init__(self, *, scopes: IWriteScopeRepository, clock: IClock) -> None:
        self._scopes = scopes
        self._clock = clock

    def retire(self, scope: Scope, ctx: ChangeContext, *, by: str, if_match: int | None = None) -> Scope:
        """Append ``scope.retired``, recording ``by`` on the fact. Retiring a retired scope writes nothing."""
        return self._record(scope, BrakeVerb.RETIRE, ctx, by=by, if_match=if_match)

    def enable(self, scope: Scope, ctx: ChangeContext, *, by: str, if_match: int | None = None) -> Scope:
        """Append ``scope.enabled``. Enabling an enabled scope writes nothing."""
        return self._record(scope, BrakeVerb.ENABLE, ctx, by=by, if_match=if_match)

    def _record(self, scope: Scope, verb: BrakeVerb, ctx: ChangeContext, *, by: str, if_match: int | None) -> Scope:
        now = self._clock.now()
        decided = scope.set_retired(verb.records_retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return scope
        moved, change = decided
        return self._scopes.record_lifecycle(
            moved, retired=verb.records_retired, from_revision=scope.revision, at=now, by=by, change=change
        )
