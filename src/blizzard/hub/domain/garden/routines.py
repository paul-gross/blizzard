"""Routine domain model — an operator-authored pointer at a graph, a default scope, and
run-defaults the hub hands back unresolved.

``routine_id`` is a surrogate key; the immutable ``name`` is the lineage and keys its change
rows (``bzh:configured-record``). :class:`RoutineAuthoring` mints an unseen default scope
and requires the named graph resolve to an enabled mint."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import ClassVar, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
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
from blizzard.hub.domain.garden.scopes import Scope, ScopeMint, ScopeRegistry, ScopeSlug
from blizzard.hub.domain.graph.harnesses import validated_harnesses
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

#: The mutable fields, in the order a diff lists them.
_FIELDS = ("graph_name", "default_scope_slug", "default_model", "default_effort", "default_harnesses")
#: The fields an edit may set but never clear.
_REQUIRED_FIELDS = ("graph_name", "default_scope_slug", "default_model", "default_harnesses")
#: The wire name a link or unlink reports the routine's linked set under.
SCOPES_FIELD = "scopes"


class RoutineNameTakenError(ValueError):
    """A routine create names an already-existing routine name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a routine named {name!r} already exists")
        self.name = name


class RoutineNameImmutableError(ValueError):
    """A routine edit tries to change the name — refused, naming the current one."""

    def __init__(self, current_name: str) -> None:
        super().__init__(f"a routine's name is immutable — currently named {current_name!r}")
        self.current_name = current_name


class RoutineGraphUnresolvedError(ValueError):
    """A routine create or edit names a graph with no enabled mint."""

    def __init__(self, graph_name: str) -> None:
        super().__init__(f"no enabled graph named {graph_name!r} exists")
        self.graph_name = graph_name


class RoutineDefaultScopeUnlinkError(ValueError):
    """An unlink names a routine's own default scope — refused, since the routine's
    default scope is always a member of its own set."""

    def __init__(self, routine_id: str, scope_slug: str) -> None:
        super().__init__(f"routine {routine_id!r}'s default scope {scope_slug!r} cannot be unlinked")
        self.routine_id = routine_id
        self.scope_slug = scope_slug


class RoutineVerb(StrEnum):
    """Every act addressed at a routine by name or id."""

    CREATE = "create"
    EDIT = "edit"
    RETIRE = "retire"
    ENABLE = "enable"
    LINK_SCOPE = "link_scope"
    UNLINK_SCOPE = "unlink_scope"
    RUN = "run"
    READ = "read"


@domain_model
@dataclass(frozen=True)
class RoutineEdit:
    """A sparse routine edit: :data:`UNSET` leaves a field. ``name`` may only restate the
    current one; ``None`` clears ``default_effort`` and is refused on every other field."""

    name: str | None | UnsetType = UNSET
    graph_name: str | None | UnsetType = UNSET
    default_scope_slug: ScopeSlug | None | UnsetType = UNSET
    default_model: Sequence[str] | None | UnsetType = UNSET
    default_effort: str | None | UnsetType = UNSET
    default_harnesses: Sequence[str] | None | UnsetType = UNSET


def require_name_free(holder: Routine | None, name: str) -> None:
    """A name already held — by an enabled or a retired routine, which keeps its name as
    lineage — refuses a create, never merged."""
    if holder is not None:
        raise RoutineNameTakenError(name)


def require_graph_resolves(graph: Graph | None, graph_name: str) -> Graph:
    """``graph`` is the enabled mint ``graph_name`` resolves to, if any; none refuses."""
    if graph is None:
        raise RoutineGraphUnresolvedError(graph_name)
    return graph


def require_graph_change_resolves(current: str | None, stated: str, resolves: Callable[[str], bool]) -> None:
    """A routine's graph is checked when it is created (``current`` is ``None``) or moved to another
    graph, and refused when ``stated`` does not resolve to an enabled one. Restating the graph a
    routine already points at is not pointing, so it is accepted without resolving."""
    if stated != current and not resolves(stated):
        raise RoutineGraphUnresolvedError(stated)


@domain_model
@dataclass(frozen=True)
class Routine:
    """A stored routine. ``retired`` derives from the newest lifecycle fact.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it,
    or ``None`` when the verb changes nothing and so writes nothing."""

    routine_id: str
    name: str
    graph_name: str
    default_scope_slug: str
    created_at: datetime
    default_model: list[str] = field(default_factory=list)
    default_effort: str | None = None
    # The routine's default harness preference, `default_model`'s shape: empty is no preference.
    default_harnesses: list[str] = field(default_factory=list)
    revision: int = 1
    retired: bool = False

    TRANSITIONS: ClassVar[Mapping[RecordState, Mapping[ChangeOp, Verdict]]] = FIELDED_RECORD_TRANSITIONS

    #: Which verbs are legal from which brake state; a retired routine refuses only a run.
    LEGAL_FROM: ClassVar[Mapping[RoutineVerb, frozenset[BrakeState]]] = {
        RoutineVerb.CREATE: frozenset(),
        RoutineVerb.EDIT: ANY_STATE,
        RoutineVerb.RETIRE: ANY_STATE,
        RoutineVerb.ENABLE: ANY_STATE,
        RoutineVerb.LINK_SCOPE: ANY_STATE,
        RoutineVerb.UNLINK_SCOPE: ANY_STATE,
        RoutineVerb.RUN: ENABLED_ONLY,
        RoutineVerb.READ: ANY_STATE,
    }

    @staticmethod
    def allows(verb: RoutineVerb, *, retired: bool) -> bool:
        """Whether ``verb`` is legal on a routine whose newest lifecycle fact reads
        ``retired``."""
        return BrakeState.of(retired=retired) in Routine.LEGAL_FROM[verb]

    @classmethod
    def new(
        cls,
        *,
        routine_id: str,
        name: str,
        graph_name: str,
        default_scope_slug: str,
        default_model: Sequence[str],
        default_effort: str | None,
        default_harnesses: Sequence[str],
        ctx: ChangeContext,
        at: datetime,
    ) -> tuple[Routine, ConfigChange]:
        """A new routine at revision 1 pointing at ``default_scope_slug``, with its ``create`` change."""
        record = cls(
            routine_id=routine_id,
            name=name,
            graph_name=graph_name,
            default_scope_slug=default_scope_slug,
            created_at=at,
            default_model=list(default_model),
            default_effort=default_effort,
            default_harnesses=validated_harnesses(list(default_harnesses)),
        )
        return record, ConfigChange.of(ctx, RecordKind.ROUTINE, name, 1, ChangeOp.CREATE, record._diff(None), at)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        if if_match is not None and if_match != self.revision:
            raise ConfigRevisionConflict("routine", self.name, current=self.revision)

    def edit(
        self, edit: RoutineEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Routine, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even
        for an edit that changes nothing; a ``name`` other than the current one refuses — a
        routine's name is its lineage."""
        self.require_revision(if_match)
        if edit.name is not UNSET and edit.name != self.name:
            raise RoutineNameImmutableError(self.name)
        merged = self._merged(edit)
        changes = merged._diff(self)
        if not changes:
            return None
        edited = replace(merged, revision=self.revision + 1)
        return edited, ConfigChange.of(ctx, RecordKind.ROUTINE, self.name, edited.revision, ChangeOp.EDIT, changes, at)

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Routine, ConfigChange] | None:
        """Retire or enable; ``None`` when the routine already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        if self.TRANSITIONS[RecordState.of(self.retired)][op] is Verdict.NO_OP:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = (FieldChange(RETIRED_FIELD, self.retired, retired),)
        return moved, ConfigChange.of(ctx, RecordKind.ROUTINE, self.name, moved.revision, op, flip, at)

    def with_scopes(
        self, linked: Sequence[str], scopes: Sequence[str], ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Routine, ConfigChange] | None:
        """Move the linked set from ``linked`` to ``scopes`` — an ``edit`` whose diff field is
        ``scopes``; ``None`` when the set is unchanged."""
        self.require_revision(if_match)
        old, new = sorted(set(linked)), sorted(set(scopes))
        if old == new:
            return None
        moved = replace(self, revision=self.revision + 1)
        changes = (FieldChange(SCOPES_FIELD, old, new),)
        return moved, ConfigChange.of(ctx, RecordKind.ROUTINE, self.name, moved.revision, ChangeOp.EDIT, changes, at)

    def require_unlinkable(self, scope: Scope) -> None:
        """The routine's own default scope never leaves its set."""
        if scope.slug == self.default_scope_slug:
            raise RoutineDefaultScopeUnlinkError(self.routine_id, scope.slug)

    def effective_scope_slug(self, override: ScopeSlug | None) -> ScopeSlug:
        """The scope a run acts on: the override when one is named, else the
        routine's own default."""
        return override if override is not None else ScopeSlug.parse(self.default_scope_slug)

    def _merged(self, edit: RoutineEdit) -> Routine:
        """This routine with every set field of ``edit`` applied; ``None`` is refused naming
        the field, except on ``default_effort``, which it clears."""
        changes: dict[str, object] = {}
        for name in _REQUIRED_FIELDS:
            value = getattr(edit, name)
            if value is None:
                raise ConfigFieldError(name, "must not be null")
            if value is not UNSET:
                changes[name] = value
        if isinstance(edit.default_scope_slug, ScopeSlug):
            changes["default_scope_slug"] = edit.default_scope_slug.value
        if "default_model" in changes:
            changes["default_model"] = list(changes["default_model"])  # type: ignore[call-overload]
        if "default_harnesses" in changes:
            changes["default_harnesses"] = validated_harnesses(list(changes["default_harnesses"]))  # type: ignore[call-overload]
        if edit.default_effort is not UNSET:
            changes["default_effort"] = edit.default_effort
        return replace(self, **changes)  # type: ignore[arg-type]

    def _diff(self, old: Routine | None) -> tuple[FieldChange, ...]:
        """The fields that differ from ``old``, by wire name. With no ``old`` (a create),
        every field is listed against ``None``."""
        changes: list[FieldChange] = []
        for name in _FIELDS:
            new_value = getattr(self, name)
            old_value = None if old is None else getattr(old, name)
            if old_value != new_value:
                changes.append(FieldChange(name, old_value, new_value))
        return tuple(changes)


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadRoutineRepository(Protocol):
    """Read-only routine access. Controllers at the edges depend on this variant."""

    def get(self, routine_id: str) -> Routine | None: ...

    def get_by_name(self, name: str) -> Routine | None: ...

    def get_many_by_name(self, names: Sequence[str]) -> dict[str, Routine]:
        """The routines holding ``names``, keyed by name — ``get_by_name``'s batched plural
        (``bzh:bulk-reconstitution``); a name no routine holds is absent."""
        ...

    def list_all(self) -> list[Routine]: ...

    def is_retired(self, routine_id: str) -> bool:
        """Whether ``routine_id``'s newest lifecycle fact reads retired.

        ``False`` for a routine with no lifecycle fact at all — every freshly minted
        routine starts enabled."""
        ...

    def retired_ids(self) -> set[str]:
        """Every routine id whose newest lifecycle fact reads retired —
        the bulk counterpart to :meth:`is_retired`, mirroring
        ``IReadScopeRepository.retired_slugs``."""
        ...


class IWriteRoutineRepository(IReadRoutineRepository, Protocol):
    """Read-write routine access. Only the domain layer depends on this variant; each write
    commits its ``change`` — and a ``scope_mint``'s own change — in the same transaction."""

    def create(self, routine: Routine, *, change: ConfigChange, scope_mint: ScopeMint | None) -> Routine:
        """Insert the routine row, mint its default scope when ``scope_mint`` is given, and
        link that default into the routine's set. A ``name`` another routine already holds —
        a create that lost the race to it — raises :class:`RoutineNameTakenError`."""
        ...

    def update(
        self, routine: Routine, *, from_revision: int, change: ConfigChange, scope_mint: ScopeMint | None
    ) -> Routine:
        """Compare-and-set ``from_revision``, writing everything but ``name``/``routine_id``,
        minting the default scope when ``scope_mint`` is given, and linking the default into
        the routine's set; :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(
        self, routine: Routine, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Routine:
        """Append the ``routine.retired``/``routine.enabled`` fact and move the revision to
        ``routine.revision``, as one compare-and-set on ``from_revision``."""
        ...


# --- The routine_scopes join seam (I-prefix, read/write split — bzh:repository-split) ---


class IReadRoutineScopeRepository(Protocol):
    """Read-only access to a routine's linked scopes. Controllers at the edges depend
    on this variant."""

    def list_scopes(self, routine_id: str) -> list[str]:
        """Every scope slug linked to ``routine_id``, sorted."""
        ...

    def list_scopes_for(self, routine_ids: Sequence[str]) -> dict[str, list[str]]:
        """``list_scopes`` for every id in ``routine_ids``, keyed by id — each id present,
        its list empty when nothing links to it."""
        ...

    def list_routines(self, scope_slug: str) -> list[str]:
        """Every routine id linked to ``scope_slug``, sorted — the reverse direction
        of :meth:`list_scopes`."""
        ...


class IWriteRoutineScopeRepository(IReadRoutineScopeRepository, Protocol):
    """Read-write access to the ``routine_scopes`` join. Only the domain layer depends
    on this variant; each write moves the routine's revision and commits ``change`` with it."""

    def link(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        """Link ``scope_slug`` into ``routine``'s set as one compare-and-set on ``from_revision``."""
        ...

    def unlink(self, routine: Routine, scope_slug: str, *, from_revision: int, change: ConfigChange) -> Routine:
        """Unlink ``scope_slug`` from ``routine``'s set as one compare-and-set on ``from_revision``."""
        ...


class RoutineAuthoring:
    """Create and edit a routine, minting its default scope on demand and linking it into
    the routine's own `routine_scopes` set, so the default is always a member of that set.
    The mint, the routine write, the link, and both change rows commit in one transaction."""

    def __init__(
        self,
        *,
        routines: IWriteRoutineRepository,
        graphs: IReadGraphRepository,
        scope_registry: ScopeRegistry,
        clock: IClock,
    ) -> None:
        self._routines = routines
        self._graphs = graphs
        self._scope_registry = scope_registry
        self._clock = clock

    def _resolves(self, graph_name: str) -> bool:
        return self._graphs.get_enabled_by_name(graph_name) is not None

    def create(
        self,
        *,
        name: str,
        graph_name: str,
        default_scope_slug: ScopeSlug,
        ctx: ChangeContext,
        default_model: list[str] | None = None,
        default_effort: str | None = None,
        default_harnesses: list[str] | None = None,
    ) -> Routine:
        harnesses = validated_harnesses(default_harnesses or [])
        require_name_free(self._routines.get_by_name(name), name)
        require_graph_change_resolves(None, graph_name, self._resolves)
        scope, scope_mint = self._scope_registry.resolve(default_scope_slug, ctx)
        routine, change = Routine.new(
            routine_id=Id.mint(IdPrefix.ROUTINE, self._clock).value,
            name=name,
            graph_name=graph_name,
            default_scope_slug=scope.slug,
            default_model=default_model or [],
            default_effort=default_effort,
            default_harnesses=harnesses,
            ctx=ctx,
            at=self._clock.now(),
        )
        # A concurrent create that took the name first surfaces from the port as
        # RoutineNameTakenError, the same refusal the pre-check above raises.
        return self._routines.create(routine, change=change, scope_mint=scope_mint)

    def edit(self, routine: Routine, edit: RoutineEdit, ctx: ChangeContext, *, if_match: int | None = None) -> Routine:
        """Apply a sparse edit. Graph resolution and default-scope minting run only when their
        field is present and, for the graph, changes it; an edit that changes nothing writes nothing. A new default is
        linked; a previous default is deliberately left linked — the routine still sweeps
        it, and a set larger than its default is legal."""
        decided = routine.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if isinstance(edit.graph_name, str):
            require_graph_change_resolves(routine.graph_name, edit.graph_name, self._resolves)
        if decided is None:
            return routine
        edited, change = decided
        scope_mint = None
        if isinstance(edit.default_scope_slug, ScopeSlug):
            _, scope_mint = self._scope_registry.resolve(edit.default_scope_slug, ctx)
        return self._routines.update(edited, from_revision=routine.revision, change=change, scope_mint=scope_mint)


class RoutineScopeMembership:
    """Manage a routine's `routine_scopes` set — link/unlink take the
    already-resolved `Routine` and `Scope` objects (`bzh:domain-takes-objects`). Each is a
    routine ``edit`` whose diff field is ``scopes``; one that changes nothing writes nothing."""

    def __init__(self, *, routine_scopes: IWriteRoutineScopeRepository, clock: IClock) -> None:
        self._routine_scopes = routine_scopes
        self._clock = clock

    def link(self, routine: Routine, scope: Scope, ctx: ChangeContext, *, if_match: int | None = None) -> Routine:
        """Link `scope` into `routine`'s set; a no-op if it is already linked."""
        linked = self._routine_scopes.list_scopes(routine.routine_id)
        decided = routine.with_scopes(linked, [*linked, scope.slug], ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return routine
        moved, change = decided
        return self._routine_scopes.link(moved, scope.slug, from_revision=routine.revision, change=change)

    def unlink(self, routine: Routine, scope: Scope, ctx: ChangeContext, *, if_match: int | None = None) -> Routine:
        """Unlink `scope` from `routine`'s set; a no-op if it is not linked. Refused when
        `scope` is `routine`'s own default: a routine's default scope is always a
        member of its own set."""
        routine.require_unlinkable(scope)
        linked = self._routine_scopes.list_scopes(routine.routine_id)
        kept = [slug for slug in linked if slug != scope.slug]
        decided = routine.with_scopes(linked, kept, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return routine
        moved, change = decided
        return self._routine_scopes.unlink(moved, scope.slug, from_revision=routine.revision, change=change)


class RoutineLifecycle:
    """Set or clear a routine's retired brake without touching its row's fields — the
    ``ScopeLifecycle`` shape."""

    def __init__(self, *, routines: IWriteRoutineRepository, clock: IClock) -> None:
        self._routines = routines
        self._clock = clock

    def retire(self, routine: Routine, ctx: ChangeContext, *, by: str, if_match: int | None = None) -> Routine:
        """Append ``routine.retired``, recording ``by`` on the fact. Retiring a retired routine writes nothing."""
        return self._record(routine, BrakeVerb.RETIRE, ctx, by=by, if_match=if_match)

    def enable(self, routine: Routine, ctx: ChangeContext, *, by: str, if_match: int | None = None) -> Routine:
        """Append ``routine.enabled``. Enabling an enabled routine writes nothing."""
        return self._record(routine, BrakeVerb.ENABLE, ctx, by=by, if_match=if_match)

    def _record(
        self, routine: Routine, verb: BrakeVerb, ctx: ChangeContext, *, by: str, if_match: int | None
    ) -> Routine:
        now = self._clock.now()
        decided = routine.set_retired(verb.records_retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return routine
        moved, change = decided
        return self._routines.record_lifecycle(
            moved, retired=verb.records_retired, from_revision=routine.revision, at=now, by=by, change=change
        )
