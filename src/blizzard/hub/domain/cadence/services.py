"""The cadence verbs — create, list, show, edit, retire, enable.

Each service loads what it needs, hands it to the model's pure functions, and passes the result to the repository
(``bzh:domain-orchestration-split``). A write that would leave a live cadence keeping a promoting chore is an
admission, refused unless the caller says the writer may admit work (``may_admit``). Every read that returns
cadences resolves their attention for the whole set at once (``bzh:bulk-reconstitution``), against the routines,
links, graphs, and scopes as they stand, never cached across reads (``bzh:config-read-on-use``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from datetime import datetime

from blizzard.foundation.cadence_attention import ErrandState
from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.errands import Errand, ErrandRun, chore_errands, due_at, errand_state
from blizzard.hub.domain.cadence.model import (
    Attention,
    AttentionFacts,
    Cadence,
    CadenceEdit,
    CadenceShape,
    CadenceSlugTakenError,
    ScopeSelection,
)
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository, IReadErrandRunsRepository, IWriteCadenceRepository
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.garden.brake import BrakeVerb
from blizzard.hub.domain.garden.routines import IReadRoutineRepository, IReadRoutineScopeRepository
from blizzard.hub.domain.garden.scopes import IReadScopeRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.projects.model import Project

#: How many of a schedule's coming moments a cadence's view carries.
NEXT_MOMENTS = 5


@domain_model
@dataclass(frozen=True)
class CadenceView:
    """A cadence beside the attention its errands need and its errands counted by state, as it stands at the
    read. ``errand_counts`` holds an entry for every state; ``next_moments`` holds the schedule's next
    :data:`NEXT_MOMENTS` moments after the read, earliest first — none with no schedule or once retired."""

    cadence: Cadence
    attention: tuple[Attention, ...]
    errand_counts: Mapping[ErrandState, int] = field(default_factory=lambda: dict.fromkeys(ErrandState, 0))
    next_moments: tuple[datetime, ...] = ()


@domain_model
@dataclass(frozen=True)
class ErrandView:
    """One errand of a cadence at a read: its state, the attention entries that stop it, its last run, and when it
    comes due next — ``None`` while running or stopped."""

    errand: Errand
    state: ErrandState
    stopping: tuple[Attention, ...]
    last_run: ErrandRun | None
    due_at: datetime | None


class CadenceAttention:
    """Resolve the attention of a set of cadences in bulk: the queries grow with the projects the set spans, never
    with its cadences or chores. A retired cadence needs none and is not read for."""

    def __init__(
        self,
        *,
        routines: IReadRoutineRepository,
        routine_scopes: IReadRoutineScopeRepository,
        scopes: IReadScopeRepository,
        graphs: IReadGraphRepository,
    ) -> None:
        self._routines = routines
        self._routine_scopes = routine_scopes
        self._scopes = scopes
        self._graphs = graphs

    def views(self, cadences: Sequence[Cadence]) -> list[CadenceView]:
        """Each of ``cadences``, in order, with its attention."""
        facts = self.facts(cadences)
        return [
            CadenceView(cadence, () if cadence.retired else cadence.attention(facts[cadence.project_id]))
            for cadence in cadences
        ]

    def facts(self, cadences: Sequence[Cadence], *, include_retired: bool = False) -> dict[str, AttentionFacts]:
        """What the live cadences among ``cadences`` — retired ones too when ``include_retired`` — are judged
        against, by project id: the routines their
        chores name, those routines' links and when each was made, which of their graphs resolve, and the scopes the
        chores and links name."""
        by_project: dict[str, list[Cadence]] = defaultdict(list)
        for cadence in cadences:
            if include_retired or not cadence.retired:
                by_project[cadence.project_id].append(cadence)
        routines = {
            project_id: self._routines.get_many_by_slug(
                project_id, sorted({chore.spec.routine_slug for c in held for chore in c.chores})
            )
            for project_id, held in by_project.items()
        }
        found = [routine for held in routines.values() for routine in held.values()]
        routine_ids = [routine.routine_id for routine in found]
        links = self._routine_scopes.links_for(routine_ids)
        linked = {routine_id: list(held) for routine_id, held in links.items()}
        linked_at = {
            routine_id: stamped
            for routine_id, held in links.items()
            if (stamped := {slug: at for slug, at in held.items() if at is not None})
        }
        enabled = self._graphs.enabled_names(sorted({routine.graph_name for routine in found}))
        facts: dict[str, AttentionFacts] = {}
        for project_id, held in by_project.items():
            named = {
                slug
                for cadence in held
                for chore in cadence.chores
                if chore.spec.selection is ScopeSelection.LISTED
                for slug in chore.spec.scopes
            }
            for routine in routines[project_id].values():
                named.add(routine.default_scope_slug)
                named.update(linked.get(routine.routine_id, ()))
            facts[project_id] = AttentionFacts(
                routines=routines[project_id],
                scopes=self._scopes.get_many(project_id, sorted(named)),
                linked=linked,
                enabled_graphs=enabled,
                linked_at=linked_at,
            )
        return facts


class CadenceReading:
    """List and show cadences with their attention and errand counts, and read one cadence's errands. Every read
    takes the clock once and reads every errand's last run in one call (``bzh:bulk-reconstitution``,
    ``bzh:injected-clock``)."""

    def __init__(
        self,
        *,
        cadences: IReadCadenceRepository,
        attention: CadenceAttention,
        runs: IReadErrandRunsRepository,
        clock: IClock,
    ) -> None:
        self._cadences = cadences
        self._attention = attention
        self._runs = runs
        self._clock = clock

    def list_cadences(self, project_id: str | None = None, *, include_retired: bool = False) -> list[CadenceView]:
        """The tenant's cadences, or ``project_id``'s, newest first; retired ones only when asked for."""
        held = [c for c in self._cadences.list_all(project_id) if include_retired or not c.retired]
        return self._views(held)

    def show(self, cadence_id: str) -> CadenceView | None:
        """One cadence with its attention; ``None`` when no cadence holds ``cadence_id``."""
        cadence = self._cadences.get(cadence_id)
        return None if cadence is None else self._views([cadence])[0]

    def view(self, cadence: Cadence) -> CadenceView:
        """``cadence`` as a write left it, with its attention."""
        return self._views([cadence])[0]

    def errands(self, cadence: Cadence) -> list[ErrandView]:
        """``cadence``'s errands exactly as the mint judges them, chore by chore in cadence order and by scope
        slug within a chore. A retired cadence's errands are all stopped."""
        return self._read([cadence])[0].errands

    def _views(self, cadences: Sequence[Cadence]) -> list[CadenceView]:
        views: list[CadenceView] = []
        for read in self._read(cadences):
            counts = dict.fromkeys(ErrandState, 0)
            for row in read.errands:
                counts[row.state] += 1
            views.append(CadenceView(read.cadence, read.attention, counts, read.next_moments))
        return views

    def _read(self, cadences: Sequence[Cadence]) -> list[_CadenceErrands]:
        """Each of ``cadences`` with its attention and errand views: configuration first, then every errand's last
        run in one call, judged against one clock read."""
        facts = self._attention.facts(cadences, include_retired=True)
        listed: list[tuple[Cadence, tuple[Attention, ...], list[Errand]]] = []
        for cadence in cadences:
            project_facts = facts[cadence.project_id]
            held = [
                errand
                for chore in cadence.chores
                for errand in sorted(chore_errands(cadence.cadence_id, chore, project_facts), key=_by_scope)
            ]
            listed.append((cadence, cadence.attention(project_facts), held))
        runs = self._runs.last_runs([errand.key for _, _, held in listed for errand in held])
        now = self._clock.now()
        return [
            _CadenceErrands(
                cadence,
                stopping,
                [_errand_view(cadence, errand, stopping, runs.get(errand.key), now) for errand in held],
                _next_moments(cadence, now),
            )
            for cadence, stopping, held in listed
        ]


def _next_moments(cadence: Cadence, now: datetime) -> tuple[datetime, ...]:
    schedule = cadence.rhythm.schedule
    if schedule is None or cadence.retired:
        return ()
    return schedule.next_moments(now, NEXT_MOMENTS)


def _by_scope(errand: Errand) -> str:
    return errand.scope_slug


@domain_model
@dataclass(frozen=True)
class _CadenceErrands:
    cadence: Cadence
    attention: tuple[Attention, ...]
    errands: list[ErrandView]
    next_moments: tuple[datetime, ...]


def _errand_view(
    cadence: Cadence, errand: Errand, stopping: tuple[Attention, ...], run: ErrandRun | None, now: datetime
) -> ErrandView:
    last_run = None if run is None else run.run
    state = errand_state(cadence.rhythm, errand, last_run, stopping=stopping, retired=cadence.retired, now=now)
    live = state in (ErrandState.STOPPED, ErrandState.RUNNING)
    return ErrandView(
        errand=errand,
        state=state,
        stopping=tuple(item for item in stopping if errand.stopped_by([item])),
        last_run=run,
        due_at=None if live else due_at(cadence.rhythm, errand, last_run),
    )


class CadenceAuthoring:
    """Create and edit a cadence. A write that leaves the cadence admitting work needs ``may_admit``; one that
    changes nothing writes nothing and asks nothing."""

    def __init__(self, *, cadences: IWriteCadenceRepository, clock: IClock) -> None:
        self._cadences = cadences
        self._clock = clock

    def create(self, shape: CadenceShape, *, project: Project, ctx: ChangeContext, may_admit: bool) -> Cadence:
        """Create a cadence in ``project``; its slug is free there, retired cadences included."""
        project_id = project.project_id
        cadence, change = Cadence.new(shape, project_id=project_id, ctx=ctx, at=self._clock.now())
        if self._cadences.get_by_slug(project_id, cadence.slug) is not None:
            raise CadenceSlugTakenError(cadence.slug)
        cadence.require_admission(may_admit=may_admit)
        return self._cadences.create(cadence, change=change)

    def edit(
        self, cadence: Cadence, edit: CadenceEdit, ctx: ChangeContext, *, may_admit: bool, if_match: int | None = None
    ) -> Cadence:
        """Apply a sparse edit; a chore whose key stays keeps its id."""
        decided = cadence.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return cadence
        edited, change = decided
        edited.require_admission(may_admit=may_admit)
        return self._cadences.update(edited, from_revision=cadence.revision, change=change)


class CadenceLifecycle:
    """Set or clear a cadence's retired brake. Retiring admits nothing, so it never asks ``may_admit``; enabling a
    cadence keeping a promoting chore does."""

    def __init__(self, *, cadences: IWriteCadenceRepository, clock: IClock) -> None:
        self._cadences = cadences
        self._clock = clock

    def retire(self, cadence: Cadence, ctx: ChangeContext, *, by: str, if_match: int | None = None) -> Cadence:
        """Append the retiring fact, recording ``by``. Retiring a retired cadence writes nothing."""
        return self._record(cadence, BrakeVerb.RETIRE, ctx, by=by, may_admit=True, if_match=if_match)

    def enable(
        self, cadence: Cadence, ctx: ChangeContext, *, by: str, may_admit: bool, if_match: int | None = None
    ) -> Cadence:
        """Append the enabling fact. Enabling an enabled cadence writes nothing."""
        return self._record(cadence, BrakeVerb.ENABLE, ctx, by=by, may_admit=may_admit, if_match=if_match)

    def _record(
        self, cadence: Cadence, verb: BrakeVerb, ctx: ChangeContext, *, by: str, may_admit: bool, if_match: int | None
    ) -> Cadence:
        now = self._clock.now()
        decided = cadence.set_retired(verb.records_retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return cadence
        moved, change = decided
        moved.require_admission(may_admit=may_admit)
        return self._cadences.record_lifecycle(
            moved, retired=verb.records_retired, from_revision=cadence.revision, at=now, by=by, change=change
        )
