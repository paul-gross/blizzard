"""The cadence verbs — create, list, show, edit, retire, enable.

Each service loads what it needs, hands it to the model's pure functions, and passes the result to the repository
(``bzh:domain-orchestration-split``). A write that would leave a live cadence keeping a promoting chore is an
admission, refused unless the caller says the writer may admit work (``may_admit``). Every read that returns
cadences resolves their attention for the whole set at once (``bzh:bulk-reconstitution``), against the routines,
links, graphs, and scopes as they stand, never cached across reads (``bzh:config-read-on-use``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.model import (
    Attention,
    AttentionFacts,
    Cadence,
    CadenceEdit,
    CadenceShape,
    CadenceSlugTakenError,
    ScopeSelection,
)
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository, IWriteCadenceRepository
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.garden.brake import BrakeVerb
from blizzard.hub.domain.garden.routines import IReadRoutineRepository, IReadRoutineScopeRepository
from blizzard.hub.domain.garden.scopes import IReadScopeRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.projects.model import Project


@domain_model
@dataclass(frozen=True)
class CadenceView:
    """A cadence beside the attention its errands need, as it stands at the read."""

    cadence: Cadence
    attention: tuple[Attention, ...]


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

    def facts(self, cadences: Sequence[Cadence]) -> dict[str, AttentionFacts]:
        """What the live cadences among ``cadences`` are judged against, by project id: the routines their chores
        name, those routines' links and when each was made, which of their graphs resolve, and the scopes the chores
        and links name."""
        by_project: dict[str, list[Cadence]] = defaultdict(list)
        for cadence in cadences:
            if not cadence.retired:
                by_project[cadence.project_id].append(cadence)
        routines = {
            project_id: self._routines.get_many_by_slug(
                project_id, sorted({chore.spec.routine_slug for c in held for chore in c.chores})
            )
            for project_id, held in by_project.items()
        }
        found = [routine for held in routines.values() for routine in held.values()]
        routine_ids = [routine.routine_id for routine in found]
        linked = self._routine_scopes.list_scopes_for(routine_ids)
        linked_at = self._routine_scopes.linked_at_for(routine_ids)
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
    """List and show cadences with their attention."""

    def __init__(self, *, cadences: IReadCadenceRepository, attention: CadenceAttention) -> None:
        self._cadences = cadences
        self._attention = attention

    def list_cadences(self, project_id: str | None = None, *, include_retired: bool = False) -> list[CadenceView]:
        """The tenant's cadences, or ``project_id``'s, newest first; retired ones only when asked for."""
        held = [c for c in self._cadences.list_all(project_id) if include_retired or not c.retired]
        return self._attention.views(held)

    def show(self, cadence_id: str) -> CadenceView | None:
        """One cadence with its attention; ``None`` when no cadence holds ``cadence_id``."""
        cadence = self._cadences.get(cadence_id)
        return None if cadence is None else self._attention.views([cadence])[0]

    def view(self, cadence: Cadence) -> CadenceView:
        """``cadence`` as a write left it, with its attention."""
        return self._attention.views([cadence])[0]


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
