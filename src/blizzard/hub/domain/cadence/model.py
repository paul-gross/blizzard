"""Cadence domain model — a project's rhythm (a schedule, a spacing, or both) and the chores that share it.

Pure values and functions: validation refuses only a shape that cannot mean anything, the chore merge keeps a
chore's id while its key stays, and attention names what stops each errand from the routines and scopes as they
stand. None reads a clock; each is handed the instant it needs."""

from __future__ import annotations

import re
from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Literal

from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.foundation.run_mode import RunMode
from blizzard.hub.domain.cadence.schedule import Schedule
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlug, ScopeSlugError

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


class AttentionReason(StrEnum):
    """Why a cadence cannot run an errand — the refusals a hand-started run would meet, shown beforehand."""

    ROUTINE_UNKNOWN = "routine_unknown"
    ROUTINE_RETIRED = "routine_retired"
    GRAPH_UNRESOLVED = "graph_unresolved"
    SCOPE_UNKNOWN = "scope_unknown"
    SCOPE_RETIRED = "scope_retired"
    SCOPE_UNLINKED = "scope_unlinked"
    NO_SCOPES = "no_scopes"
    NO_CHORES = "no_chores"


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
        scopes = tuple(_slug(slug, f"{where}.scopes[{i}]") for i, slug in enumerate(shape.scopes))
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
    mint."""

    routines: Mapping[str, Routine]
    scopes: Mapping[str, Scope]
    linked: Mapping[str, Collection[str]]
    enabled_graphs: Collection[str]


def _scope_attention(chore: ChoreSpec, routine: Routine, facts: AttentionFacts) -> list[Attention]:
    swept = {*facts.linked.get(routine.routine_id, ()), routine.default_scope_slug}
    if chore.selection is ScopeSelection.DEFAULT:
        default = facts.scopes.get(routine.default_scope_slug)
        if default is not None and default.retired:
            return [Attention(AttentionReason.SCOPE_RETIRED, chore.key, routine.default_scope_slug)]
        return []
    if chore.selection is ScopeSelection.ALL:
        live = [slug for slug in swept if (scope := facts.scopes.get(slug)) is not None and not scope.retired]
        return [] if live else [Attention(AttentionReason.NO_SCOPES, chore.key)]
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


@domain_model
@dataclass(frozen=True)
class Cadence:
    """A stored cadence: a project's rhythm and the chores sharing it, in the order written. ``retired``
    derives from the newest lifecycle fact."""

    cadence_id: str
    slug: str
    name: str
    rhythm: Rhythm
    chores: tuple[Chore, ...]
    created_at: datetime
    project_id: str
    revision: int = 1
    retired: bool = False

    def admits(self) -> bool:
        """Whether the cadence, as it stands, would admit work to the ready queue: live, keeping a promoting
        chore."""
        return not self.retired and any(chore.spec.promote for chore in self.chores)

    def attention(self, facts: AttentionFacts) -> tuple[Attention, ...]:
        """What stops this cadence's errands, read against ``facts``."""
        return attention(self.chores, retired=self.retired, facts=facts)
