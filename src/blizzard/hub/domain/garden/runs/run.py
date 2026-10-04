"""Routine run — mint and ingest a hub work item from a routine in one act:
``blizzard hub routine run <name>``.

Takes an already-resolved routine and an already-resolved, already-related scope
(``bzh:domain-takes-objects``): both must already exist and the scope
must already relate to the routine. Settles the mode against the pair's recorded
baseline, composes the charge, and drives the one-act write atomically. A scope outside
the routine's own related set, a retired scope, or an unresolvable graph refuses rather
than defaults."""

from __future__ import annotations

from collections.abc import Collection
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.chunk.model import HubWorkItem, IWriteWorkItemRepository, WorkItemAuthor
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.garden.findings.model import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.garden.routines import (
    IReadRoutineRepository,
    IReadRoutineScopeRepository,
    Routine,
    RoutineVerb,
    RunMode,
    require_graph_resolves,
)
from blizzard.hub.domain.garden.scopes import IReadScopeRepository, Scope, ScopeVerb
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.work_items.editing import prepare_mint


class RoutineRetiredError(ValueError):
    """A run is addressed at a retired routine: refused first, before any
    scope check — a routine that cannot run at all makes no claim about a scope."""

    def __init__(self, name: str) -> None:
        super().__init__(f"routine {name!r} is retired")
        self.name = name


class ScopeRetiredError(ValueError):
    """A run's effective scope — named or the routine's own default — is retired:
    a real, named resource in a blocked state, refused rather than run against."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"scope {slug!r} is retired")
        self.slug = slug


class ScopeNotRelatedError(ValueError):
    """A run's effective scope is not a member of the routine's own related set:
    refused rather than run against, and never minted. The
    routine's own default is always a member, so this can only fire on an explicit
    override."""

    def __init__(self, routine_id: str, slug: str) -> None:
        super().__init__(f"scope {slug!r} is not related to routine {routine_id!r}")
        self.routine_id = routine_id
        self.slug = slug


def compose_charge(
    *,
    routine_name: str,
    graph_name: str,
    scope_slug: str,
    scope_description: str,
    mode: RunMode,
    downgraded: bool,
    baseline: FindingSet | None,
    note: str | None,
) -> str:
    """The run's charge as prose — names the routine and the graph its runs
    execute, the scope with its own description, the mode with its resolved baseline,
    and ``note`` as a "This run" section. A pure function over already-resolved values —
    no store — so a unit test drives it directly."""
    lines = [f"Routine: {routine_name} (graph: {graph_name})"]
    lines.append(f"Scope: {scope_slug} — {scope_description}" if scope_description else f"Scope: {scope_slug}")
    if mode is RunMode.DELTA:
        if baseline is None:
            raise ValueError("a delta mode with no baseline must have already downgraded to full")
        revisions = ", ".join(f"{repo}@{rev}" for repo, rev in sorted(baseline.revisions.items()))
        lines.append(f"Mode: delta — baseline {baseline.finding_set_id} ({revisions or 'no repositories recorded'})")
    elif downgraded:
        lines.append("Mode: full (downgraded from delta — the routine/scope pair has recorded no baseline yet)")
    else:
        lines.append("Mode: full")
    if note:
        lines.extend(["", "This run", note])
    return "\n".join(lines)


def require_routine_runnable(routine: Routine, *, retired: bool) -> None:
    """A retired routine refuses a run — checked before anything about its scope."""
    if not Routine.allows(RoutineVerb.RUN, retired=retired):
        raise RoutineRetiredError(routine.name)


def require_related(routine: Routine, scope: Scope, linked: Collection[str]) -> None:
    """The effective scope must be in the routine's declared set, ``linked``."""
    if scope.slug not in linked:
        raise ScopeNotRelatedError(routine.routine_id, scope.slug)


def require_scope_open(scope: Scope, *, retired: bool) -> None:
    """A retired effective scope refuses a run."""
    if not Scope.allows(ScopeVerb.RUN_AGAINST, retired=retired):
        raise ScopeRetiredError(scope.slug)


def settle_mode(requested: RunMode, baseline: FindingSet | None) -> tuple[RunMode, bool]:
    """The mode a run settles to and whether it was downgraded: a delta with no
    recorded baseline for the pair runs full, on the record, never refused."""
    if requested is RunMode.DELTA and baseline is None:
        return RunMode.FULL, True
    return requested, False


@domain_model
@dataclass(frozen=True)
class RunPlan:
    """Everything a run decides before it mints: the graph it executes, how the
    requested mode settled, and the work item's title and charge."""

    graph: Graph
    effective_mode: RunMode
    downgraded: bool
    baseline: FindingSet | None
    title: str
    charge: str


def plan_run(
    routine: Routine,
    *,
    routine_retired: bool,
    graph: Graph | None,
    scope: Scope,
    scope_retired: bool,
    linked: Collection[str],
    requested_mode: RunMode,
    baseline: FindingSet | None,
    note: str | None,
) -> RunPlan:
    """Decide one run of ``routine`` against ``scope`` from already-loaded values.

    Refusals in order: a retired routine, an unresolved graph, a scope outside the
    routine's declared set, a retired scope — a routine that cannot run makes no claim
    about a scope, and the brake never judges a scope the routine has no set claim on."""
    require_routine_runnable(routine, retired=routine_retired)
    resolved = require_graph_resolves(graph, routine.graph_name)
    require_related(routine, scope, linked)
    require_scope_open(scope, retired=scope_retired)
    effective_mode, downgraded = settle_mode(requested_mode, baseline)
    charge = compose_charge(
        routine_name=routine.name,
        graph_name=routine.graph_name,
        scope_slug=scope.slug,
        scope_description=scope.description,
        mode=effective_mode,
        downgraded=downgraded,
        baseline=baseline,
        note=note,
    )
    return RunPlan(
        graph=resolved,
        effective_mode=effective_mode,
        downgraded=downgraded,
        baseline=baseline,
        title=f"{routine.name} run ({effective_mode.value})",
        charge=charge,
    )


@dto
@dataclass(frozen=True)
class RunResult:
    """The result of one routine run — the minted item, its chunk, and how the
    requested mode settled."""

    item: HubWorkItem
    chunk_id: str
    effective_mode: RunMode
    downgraded: bool
    baseline: FindingSet | None


class RunService:
    """Mint and ingest a hub work item from a routine, in one act."""

    def __init__(
        self,
        *,
        routines: IReadRoutineRepository,
        scopes: IReadScopeRepository,
        routine_scopes: IReadRoutineScopeRepository,
        graphs: IReadGraphRepository,
        finding_sets: IReadFindingSetRepository,
        items: IWriteWorkItemRepository,
        work_refs: IReadChunkWorkRefsRepository,
        clock: IClock,
    ) -> None:
        self._routines = routines
        self._scopes = scopes
        self._routine_scopes = routine_scopes
        self._graphs = graphs
        self._finding_sets = finding_sets
        self._items = items
        self._work_refs = work_refs
        self._clock = clock

    def refuse_if_retired(self, routine: Routine) -> None:
        """The retired-routine refusal on its own, so an edge can apply it before it
        resolves the run's scope — :meth:`run` applies it again first thing."""
        require_routine_runnable(routine, retired=self._routines.is_retired(routine.routine_id))

    def run(
        self,
        routine: Routine,
        *,
        scope: Scope,
        mode: RunMode,
        note: str | None,
        author: WorkItemAuthor,
    ) -> RunResult:
        plan = plan_run(
            routine,
            routine_retired=self._routines.is_retired(routine.routine_id),
            graph=self._graphs.get_enabled_by_name(routine.graph_name),
            scope=scope,
            scope_retired=self._scopes.is_retired(scope.slug),
            linked=self._routine_scopes.list_scopes(routine.routine_id),
            requested_mode=mode,
            baseline=self._finding_sets.newest_for_routine_scope(routine.name, scope.slug),
            note=note,
        )
        pointer, chunk, pointer_at = prepare_mint(
            self._items,
            self._work_refs,
            self._clock,
            RESERVED_HUB_SOURCE_NAME,
            graph=plan.graph,
            default_model=routine.default_model,
            default_effort=routine.default_effort,
            default_harnesses=routine.default_harnesses,
        )
        item = self._items.create_run_with_chunk(
            pointer=pointer,
            title=plan.title,
            body=plan.charge,
            author=author,
            routine_name=routine.name,
            scope_slug=scope.slug,
            run_mode=plan.effective_mode.value,
            at=pointer_at,
            chunk=chunk,
        )
        return RunResult(
            item=item,
            chunk_id=chunk.chunk_id,
            effective_mode=plan.effective_mode,
            downgraded=plan.downgraded,
            baseline=plan.baseline,
        )
