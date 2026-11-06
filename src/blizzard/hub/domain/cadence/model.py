"""Cadence domain model — a project's rhythm (a schedule, a spacing, or both) and the chores that share it.

Pure values and functions: validation refuses only a shape that cannot mean anything, the chore merge keeps a
chore's id while its key stays, and attention names what stops each errand from the routines and scopes as they
stand. None reads a clock; each is handed the instant it needs."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal

from blizzard.foundation.cadence_attention import AttentionReason
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.cadence.schedule import Schedule
from blizzard.hub.domain.config import lifecycle
from blizzard.hub.domain.config.changes import (
    RETIRED_FIELD,
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug, ScopeSlugError
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType

_SPACING_PATTERN = re.compile(r"^([0-9]+)([mhd])$")
_UNIT_MINUTES = {"m": 1, "h": 60, "d": 24 * 60}
#: The selection a chore states to run over every scope its routine sweeps.
ALL_SCOPES = "all"


class Anchor(StrEnum):
    """Which moment of an errand's last run its spacing counts from."""

    SCHEDULED = "scheduled"
    STARTED = "started"
    FINISHED = "finished"


class ScopeSelection(StrEnum):
    """Which scopes a chore runs over: every scope its routine sweeps, the listed ones, or its routine's default."""

    ALL = "all"
    LISTED = "listed"
    DEFAULT = "default"


@domain_model
@dataclass(frozen=True)
class Spacing:
    """The least time between an errand's runs, in whole minutes — the only way to obtain one is :meth:`parse`."""

    minutes: int

    @classmethod
    def parse(cls, raw: str) -> Spacing:
        """A whole number and one unit, ``m``, ``h``, or ``d``, of at least one minute."""
        match = _SPACING_PATTERN.match(raw)
        if match is None:
            raise ConfigFieldError("spacing", f"a spacing is a whole number and m, h, or d, got {raw!r}")
        minutes = int(match.group(1)) * _UNIT_MINUTES[match.group(2)]
        if minutes < 1:
            raise ConfigFieldError("spacing", f"a spacing is at least one minute, got {raw!r}")
        return cls(minutes)


@domain_model
@dataclass(frozen=True)
class Rhythm:
    """When a cadence's errands may come due: a schedule, a spacing, or both, and the anchor a spacing counts
    from. ``anchor`` is ``None`` when unstated, read as :attr:`Anchor.STARTED`."""

    schedule: Schedule | None
    spacing: Spacing | None
    anchor: Anchor | None = None

    def effective_anchor(self) -> Anchor | None:
        """The anchor the spacing counts from — ``started`` when unstated — or ``None`` with no spacing."""
        if self.spacing is None:
            return None
        return self.anchor or Anchor.STARTED

    @classmethod
    def parse(
        cls,
        *,
        cron: str | None,
        zone: str | None,
        has_schedule: bool,
        spacing: str | None,
        anchor: str | None,
        at: datetime,
    ) -> Rhythm:
        """The stated rhythm, refused naming the field when nothing would ever come due, the schedule or spacing
        is malformed, or an anchor is stated without a spacing or outside the three anchors. ``has_schedule`` says
        a schedule table was stated, even an empty one; ``at`` is the instant the never-firing check reads from."""
        schedule = Schedule.parse(cron, zone, at=at) if has_schedule else None
        parsed_spacing = Spacing.parse(spacing) if spacing is not None else None
        if schedule is None and parsed_spacing is None:
            raise ConfigFieldError("schedule", "a cadence states a schedule, a spacing, or both")
        parsed_anchor: Anchor | None = None
        if anchor is not None:
            if parsed_spacing is None:
                raise ConfigFieldError("anchor", "an anchor counts a spacing, and this cadence states none")
            try:
                parsed_anchor = Anchor(anchor)
            except ValueError as exc:
                raise ConfigFieldError(
                    "anchor", f"an anchor is scheduled, started, or finished, got {anchor!r}"
                ) from exc
        return cls(schedule, parsed_spacing, parsed_anchor)


@domain_model
@dataclass(frozen=True)
class ChoreShape:
    """One chore as written, before validation. ``scopes`` is ``"all"``, a list of slugs, or ``None`` for the
    routine's default scope; every other ``None`` is a field left unstated."""

    key: str | None
    routine: str | None
    scopes: Literal["all"] | Sequence[str] | None = None
    mode: str | None = None
    note: str | None = None
    promote: bool | None = None


@domain_model
@dataclass(frozen=True)
class ChoreSpec:
    """A validated chore, without the identity the merge gives it. ``scopes`` holds slugs only when ``selection``
    is :attr:`ScopeSelection.LISTED`."""

    key: str
    routine_slug: str
    selection: ScopeSelection
    scopes: tuple[str, ...]
    mode: RunMode
    note: str | None
    promote: bool

    def names_same_scopes(self, other: ChoreSpec) -> bool:
        """Whether ``other`` names the same scope set — the selection and, for a listed one, the slugs."""
        return self.selection == other.selection and set(self.scopes) == set(other.scopes)


def _slug(raw: str, field: str) -> str:
    try:
        return ScopeSlug.parse(raw).value
    except ScopeSlugError as exc:
        raise ConfigFieldError(field, str(exc)) from exc


def _chore_spec(shape: ChoreShape, where: str) -> ChoreSpec:
    if not shape.key:
        raise ConfigFieldError(f"{where}.key", "a chore states its key")
    key = _slug(shape.key, f"{where}.key")
    if not shape.routine:
        raise ConfigFieldError(f"{where}.routine", "a chore states the routine it runs")
    if shape.scopes is None:
        selection, scopes = ScopeSelection.DEFAULT, ()
    elif isinstance(shape.scopes, str):
        if shape.scopes != ALL_SCOPES:
            raise ConfigFieldError(f"{where}.scopes", f"scopes is 'all' or a list of slugs, got {shape.scopes!r}")
        selection, scopes = ScopeSelection.ALL, ()
    else:
        if not shape.scopes:
            raise ConfigFieldError(f"{where}.scopes", "a scope list names at least one scope")
        selection = ScopeSelection.LISTED
        # A listed set has no order; it is held sorted, each slug once.
        scopes = tuple(sorted({_slug(slug, f"{where}.scopes[{i}]") for i, slug in enumerate(shape.scopes)}))
    try:
        mode = RunMode(shape.mode) if shape.mode is not None else RunMode.FULL
    except ValueError as exc:
        raise ConfigFieldError(f"{where}.mode", f"a mode is full or delta, got {shape.mode!r}") from exc
    promote = True if shape.promote is None else shape.promote
    return ChoreSpec(key, shape.routine, selection, scopes, mode, shape.note, promote)


def parse_chores(shapes: Sequence[ChoreShape]) -> tuple[ChoreSpec, ...]:
    """The chores as written, in their order, refused naming the field on a missing key or routine, a key or
    listed scope that is not a slug, an empty scope list, a mode outside ``full`` and ``delta``, or a shared key."""
    specs: list[ChoreSpec] = []
    seen: set[str] = set()
    for index, shape in enumerate(shapes):
        where = f"chores[{index}]"
        spec = _chore_spec(shape, where)
        if spec.key in seen:
            raise ConfigFieldError(f"{where}.key", f"two chores share the key {spec.key!r}")
        seen.add(spec.key)
        specs.append(spec)
    return tuple(specs)


@domain_model
@dataclass(frozen=True)
class Chore:
    """A stored chore: its spec, the ``chr_`` id minted when its key first appeared in its cadence, and the
    instant it last changed which scopes it names."""

    chore_id: str
    spec: ChoreSpec
    scopes_set_at: datetime

    @property
    def key(self) -> str:
        return self.spec.key


@domain_model
@dataclass(frozen=True)
class ChoreMerge:
    """The chores after an edit, in the order written, and the keys it added, removed, and changed."""

    chores: tuple[Chore, ...]
    added: tuple[str, ...]
    removed: tuple[str, ...]
    changed: tuple[str, ...]


def merge_chores(stored: Sequence[Chore], declared: Sequence[ChoreSpec], *, at: datetime) -> ChoreMerge:
    """``declared`` matched to ``stored`` by key. A key that stays keeps its id, restamping ``scopes_set_at`` at
    ``at`` only when its named scope set changes; a new key mints a ``chr_`` id stamped at ``at``; a key no longer
    declared is removed."""
    by_key = {chore.key: chore for chore in stored}
    chores: list[Chore] = []
    added: list[str] = []
    changed: list[str] = []
    for spec in declared:
        held = by_key.get(spec.key)
        if held is None:
            chores.append(Chore(Id.mint_at(IdPrefix.CHORE, at).value, spec, at))
            added.append(spec.key)
            continue
        if held.spec == spec:
            chores.append(held)
            continue
        scopes_set_at = held.scopes_set_at if held.spec.names_same_scopes(spec) else at
        chores.append(replace(held, spec=spec, scopes_set_at=scopes_set_at))
        changed.append(spec.key)
    declared_keys = {spec.key for spec in declared}
    removed = tuple(chore.key for chore in stored if chore.key not in declared_keys)
    return ChoreMerge(tuple(chores), tuple(added), removed, tuple(changed))


@domain_model
@dataclass(frozen=True)
class Attention:
    """One thing that stops a cadence's errands: the chore by key (``None`` for the whole cadence), the scope
    when it stops one errand, and why."""

    reason: AttentionReason
    chore: str | None = None
    scope: str | None = None


@domain_model
@dataclass(frozen=True)
class AttentionFacts:
    """What attention reads, resolved in bulk for the cadences of one project: its routines and scopes by slug,
    the scope slugs each routine sweeps by routine id, and the names of the graphs that resolve to an enabled
    mint. ``linked_at`` holds, by routine id then scope slug, the instant each link was made; a link made before
    links were stamped is absent, and reads as linked before any chore."""

    routines: Mapping[str, Routine]
    scopes: Mapping[str, Scope]
    linked: Mapping[str, Collection[str]]
    enabled_graphs: Collection[str]
    linked_at: Mapping[str, Mapping[str, datetime]] = field(default_factory=dict[str, Mapping[str, datetime]])


def swept_slugs(routine: Routine, linked: Mapping[str, Collection[str]]) -> frozenset[str]:
    """The scope slugs ``routine`` sweeps now — those linked to it, with its default scope always among them —
    before any is checked against the project's scopes."""
    return frozenset({*linked.get(routine.routine_id, ()), routine.default_scope_slug})


def _scope_attention(chore: ChoreSpec, routine: Routine, facts: AttentionFacts) -> list[Attention]:
    swept = swept_slugs(routine, facts.linked)
    if chore.selection is ScopeSelection.DEFAULT:
        default = facts.scopes.get(routine.default_scope_slug)
        if default is not None and default.retired:
            return [Attention(AttentionReason.SCOPE_RETIRED, chore.key, routine.default_scope_slug)]
        return []
    if chore.selection is ScopeSelection.ALL:
        return [] if live_swept_scopes(routine, facts) else [Attention(AttentionReason.NO_SCOPES, chore.key)]
    found: list[Attention] = []
    for slug in chore.scopes:
        scope = facts.scopes.get(slug)
        if scope is None:
            found.append(Attention(AttentionReason.SCOPE_UNKNOWN, chore.key, slug))
        elif scope.retired:
            found.append(Attention(AttentionReason.SCOPE_RETIRED, chore.key, slug))
        elif slug not in swept:
            found.append(Attention(AttentionReason.SCOPE_UNLINKED, chore.key, slug))
    return found


def live_swept_scopes(routine: Routine, facts: AttentionFacts) -> tuple[Scope, ...]:
    """The scopes ``routine`` sweeps that its project knows and has not retired, by slug — the set an ``all`` chore
    runs over."""
    swept = swept_slugs(routine, facts.linked)
    return tuple(scope for slug in sorted(swept) if (scope := facts.scopes.get(slug)) is not None and not scope.retired)


def attention(chores: Sequence[Chore], *, retired: bool, facts: AttentionFacts) -> tuple[Attention, ...]:
    """What stops the errands of a cadence keeping ``chores``, empty when nothing does or the cadence is
    retired. An unknown routine stops its chore outright; a known one is checked for its brake, its graph, and
    the scopes the chore names."""
    if retired:
        return ()
    if not chores:
        return (Attention(AttentionReason.NO_CHORES),)
    found: list[Attention] = []
    for chore in chores:
        routine = facts.routines.get(chore.spec.routine_slug)
        if routine is None:
            found.append(Attention(AttentionReason.ROUTINE_UNKNOWN, chore.key))
            continue
        if routine.retired:
            found.append(Attention(AttentionReason.ROUTINE_RETIRED, chore.key))
        if routine.graph_name not in facts.enabled_graphs:
            found.append(Attention(AttentionReason.GRAPH_UNRESOLVED, chore.key))
        found.extend(_scope_attention(chore.spec, routine, facts))
    return tuple(found)


class CadenceSlugTakenError(ValueError):
    """A cadence create names a slug its project already holds, retired cadences included."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"a cadence with slug {slug!r} already exists in this project")
        self.slug = slug


class CadenceAdmissionRefused(PermissionError):
    """A write would leave a live cadence keeping a promoting chore, and the writer may not admit work to the ready
    queue."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"cadence {slug!r} would admit work to the ready queue, which this identity may not do")
        self.slug = slug


@domain_model
@dataclass(frozen=True)
class ScheduleShape:
    """A schedule table as written, before validation; ``None`` is a field left unstated."""

    cron: str | None
    zone: str | None


@domain_model
@dataclass(frozen=True)
class CadenceShape:
    """A cadence as a create writes it, before validation. ``schedule`` is ``None`` when no table is stated, and
    ``name`` when the slug stands for it."""

    slug: str | None
    name: str | None = None
    schedule: ScheduleShape | None = None
    spacing: str | None = None
    anchor: str | None = None
    chores: Sequence[ChoreShape] = ()


@domain_model
@dataclass(frozen=True)
class CadenceEdit:
    """A sparse cadence edit: :data:`UNSET` leaves a field, ``None`` clears ``schedule``, ``spacing``, or
    ``anchor`` and is refused on ``name``. ``chores``, when stated, is the whole list, matched to the stored chores
    by key."""

    name: str | None | UnsetType = UNSET
    schedule: ScheduleShape | None | UnsetType = UNSET
    spacing: str | None | UnsetType = UNSET
    anchor: str | None | UnsetType = UNSET
    chores: Sequence[ChoreShape] | UnsetType = UNSET

    def touches_rhythm(self) -> bool:
        return any(value is not UNSET for value in (self.schedule, self.spacing, self.anchor))


#: The field a chore's diff entry is named under, before its key.
CHORES_FIELD = "chores"


def _schedule_value(rhythm: Rhythm) -> dict[str, str] | None:
    schedule = rhythm.schedule
    return None if schedule is None else {"cron": schedule.cron, "zone": schedule.zone}


def _chore_value(spec: ChoreSpec) -> dict[str, object]:
    """A chore as its change row reports it, in the shape it is written."""
    scopes: object = {
        ScopeSelection.ALL: ALL_SCOPES,
        ScopeSelection.LISTED: list(spec.scopes),
        ScopeSelection.DEFAULT: None,
    }[spec.selection]
    return {
        "routine": spec.routine_slug,
        "scopes": scopes,
        "mode": spec.mode.value,
        "note": spec.note,
        "promote": spec.promote,
    }


def _chore_changes(stored: Sequence[Chore], merge: ChoreMerge) -> tuple[FieldChange, ...]:
    """One entry per chore added, changed, or removed, named ``chores.<key>``, then ``chores.order`` when the
    chores kept on both sides are written in another order."""
    held = {chore.key: chore.spec for chore in stored}
    written = {chore.key: chore.spec for chore in merge.chores}
    entries = [
        *(FieldChange(f"{CHORES_FIELD}.{key}", None, _chore_value(written[key])) for key in merge.added),
        *(
            FieldChange(f"{CHORES_FIELD}.{key}", _chore_value(held[key]), _chore_value(written[key]))
            for key in merge.changed
        ),
        *(FieldChange(f"{CHORES_FIELD}.{key}", _chore_value(held[key]), None) for key in merge.removed),
    ]
    old_order = [chore.key for chore in stored if chore.key in written]
    new_order = [chore.key for chore in merge.chores if chore.key in held]
    if old_order != new_order:
        entries.append(FieldChange(f"{CHORES_FIELD}.order", old_order, new_order))
    return tuple(entries)


def _parse_rhythm(schedule: ScheduleShape | None, spacing: str | None, anchor: str | None, *, at: datetime) -> Rhythm:
    return Rhythm.parse(
        cron=schedule.cron if schedule is not None else None,
        zone=schedule.zone if schedule is not None else None,
        has_schedule=schedule is not None,
        spacing=spacing,
        anchor=anchor,
        at=at,
    )


@domain_model
@dataclass(frozen=True)
class Cadence:
    """A stored cadence: a project's rhythm and the chores sharing it, in the order written. ``retired``
    derives from the newest lifecycle fact.

    Each verb returns the record to write with the :class:`ConfigChange` committed beside it, or ``None`` when the
    verb changes nothing and so writes nothing."""

    cadence_id: str
    slug: str
    name: str
    rhythm: Rhythm
    chores: tuple[Chore, ...]
    created_at: datetime
    project_id: str
    revision: int = 1
    retired: bool = False

    @property
    def handle(self) -> str:
        """The key the cadence's change rows and document entry carry."""
        return self.slug

    @classmethod
    def new(
        cls, shape: CadenceShape, *, project_id: str, ctx: ChangeContext, at: datetime
    ) -> tuple[Cadence, ConfigChange]:
        """A new cadence of ``project_id`` at revision 1 with its ``create`` change, refused naming the field when
        its shape cannot mean anything. Its id and every chore's are minted here, from ``at``."""
        if not shape.slug:
            raise ConfigFieldError("slug", "a cadence states its slug")
        slug = _slug(shape.slug, "slug")
        rhythm = _parse_rhythm(shape.schedule, shape.spacing, shape.anchor, at=at)
        merge = merge_chores((), parse_chores(shape.chores), at=at)
        record = cls(
            cadence_id=Id.mint_at(IdPrefix.CADENCE, at).value,
            slug=slug,
            name=shape.name or slug,
            rhythm=rhythm,
            chores=merge.chores,
            created_at=at,
            project_id=project_id,
        )
        changes = (*record._fields_diff(None), *_chore_changes((), merge))
        return record, record._change(ctx, 1, ChangeOp.CREATE, changes, at)

    def admits(self) -> bool:
        """Whether the cadence, as it stands, would admit work to the ready queue: live, keeping a promoting
        chore."""
        return not self.retired and any(chore.spec.promote for chore in self.chores)

    def require_admission(self, *, may_admit: bool) -> None:
        """:class:`CadenceAdmissionRefused` when this cadence admits work and the writer may not."""
        if self.admits() and not may_admit:
            raise CadenceAdmissionRefused(self.slug)

    def attention(self, facts: AttentionFacts) -> tuple[Attention, ...]:
        """What stops this cadence's errands, read against ``facts``."""
        return attention(self.chores, retired=self.retired, facts=facts)

    def require_revision(self, if_match: int | None) -> None:
        """:class:`ConfigRevisionConflict` when ``if_match`` names a revision other than the stored one."""
        lifecycle.require_revision(if_match, self.revision, kind="cadence", key=self.slug)

    def edit(
        self, edit: CadenceEdit, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Cadence, ConfigChange] | None:
        """Apply a sparse edit, legal from either state. The revision check comes first, even for an edit that
        changes nothing; the merged rhythm and chores are validated as a create's are."""
        self.require_revision(if_match)
        if edit.name is None:
            raise ConfigFieldError("name", "must not be null")
        name = self.name if edit.name is UNSET else edit.name
        rhythm = self._merged_rhythm(edit, at=at) if edit.touches_rhythm() else self.rhythm
        if edit.chores is UNSET:
            merge = ChoreMerge(self.chores, (), (), ())
        else:
            merge = merge_chores(self.chores, parse_chores(edit.chores), at=at)
        merged = replace(self, name=name, rhythm=rhythm, chores=merge.chores)
        changes = (*merged._fields_diff(self), *_chore_changes(self.chores, merge))
        if not changes:
            return None
        edited = replace(merged, revision=self.revision + 1)
        return edited, self._change(ctx, edited.revision, ChangeOp.EDIT, changes, at)

    def set_retired(
        self, retired: bool, ctx: ChangeContext, *, if_match: int | None, at: datetime
    ) -> tuple[Cadence, ConfigChange] | None:
        """Retire or enable; ``None`` when the cadence already stands there (a redundant verb writes nothing)."""
        self.require_revision(if_match)
        op = lifecycle.transition(self.retired, retired)
        if op is None:
            return None
        moved = replace(self, revision=self.revision + 1, retired=retired)
        flip = lifecycle.field_changes(((RETIRED_FIELD, self.retired, retired),))
        return moved, self._change(ctx, moved.revision, op, flip, at)

    def _merged_rhythm(self, edit: CadenceEdit, *, at: datetime) -> Rhythm:
        """The stored rhythm with each stated field of ``edit`` over it, validated whole."""
        held = self.rhythm
        held_schedule = None if held.schedule is None else ScheduleShape(held.schedule.cron, held.schedule.zone)
        held_spacing = None if held.spacing is None else f"{held.spacing.minutes}m"
        held_anchor = None if held.anchor is None else held.anchor.value
        return _parse_rhythm(
            held_schedule if edit.schedule is UNSET else edit.schedule,
            held_spacing if edit.spacing is UNSET else edit.spacing,
            held_anchor if edit.anchor is UNSET else edit.anchor,
            at=at,
        )

    def _fields_diff(self, old: Cadence | None) -> tuple[FieldChange, ...]:
        """The cadence's own fields that differ from ``old``, by wire name; with no ``old`` (a create), every
        field against ``None``."""

        def values(record: Cadence) -> tuple[object, ...]:
            rhythm = record.rhythm
            spacing = None if rhythm.spacing is None else rhythm.spacing.minutes
            anchor = None if rhythm.anchor is None else rhythm.anchor.value
            return record.name, _schedule_value(rhythm), spacing, anchor

        names = ("name", "schedule", "spacing_minutes", "anchor")
        old_values = (None,) * len(names) if old is None else values(old)
        return lifecycle.field_changes(zip(names, old_values, values(self), strict=True))

    def _change(
        self, ctx: ChangeContext, revision: int, op: ChangeOp, changes: tuple[FieldChange, ...], at: datetime
    ) -> ConfigChange:
        """The change row a verb on this cadence writes, keyed by its slug, in the cadence's project."""
        return ConfigChange.of(
            ctx, RecordKind.CADENCE, self.slug, revision, op, changes, at, project_id=self.project_id
        )
