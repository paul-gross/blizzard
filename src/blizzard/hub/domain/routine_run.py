"""Routine run — mint, ingest, and promote a hub work item from a routine in one act
(blizzard#392): ``blizzard hub routine run <name>``.

Takes an already-resolved routine (``bzh:domain-takes-objects``), settles the mode against
the pair's recorded baseline, composes the charge, and drives the one-act write
atomically. A retired scope or an unresolvable graph refuses rather than defaults (D5)."""

from __future__ import annotations

from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.findings import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.graph import IReadGraphRepository
from blizzard.hub.domain.promote import tail_position
from blizzard.hub.domain.routines import Routine, RoutineGraphUnresolvedError, RunMode
from blizzard.hub.domain.scopes import IReadScopeRepository, ScopeRegistry, ScopeSlug
from blizzard.hub.domain.work import IReadChunkRepository, IWriteWorkItemRepository, WorkItemAuthor, WorkItemRecord
from blizzard.hub.domain.work_items import prepare_mint


class ScopeRetiredError(ValueError):
    """A run's effective scope — named or the routine's own default — is retired (D5):
    a real, named resource in a blocked state, refused rather than run against."""

    def __init__(self, slug: str) -> None:
        super().__init__(f"scope {slug!r} is retired")
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
    """The run's charge as prose (D1) — names the routine and the graph its runs
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


@dataclass(frozen=True)
class RunResult:
    """The result of one routine run — the minted item, its chunk, and how the
    requested mode settled."""

    item: WorkItemRecord
    chunk_id: str
    promoted_id: int | None
    effective_mode: RunMode
    downgraded: bool
    baseline: FindingSet | None


class RunService:
    """Mint, ingest, and promote a hub work item from a routine, in one act (blizzard#392)."""

    def __init__(
        self,
        *,
        scopes: IReadScopeRepository,
        scope_registry: ScopeRegistry,
        graphs: IReadGraphRepository,
        finding_sets: IReadFindingSetRepository,
        items: IWriteWorkItemRepository,
        chunks: IReadChunkRepository,
        clock: IClock,
    ) -> None:
        self._scopes = scopes
        self._scope_registry = scope_registry
        self._graphs = graphs
        self._finding_sets = finding_sets
        self._items = items
        self._chunks = chunks
        self._clock = clock

    def run(
        self,
        routine: Routine,
        *,
        scope_slug: ScopeSlug | None,
        mode: RunMode,
        note: str | None,
        author: WorkItemAuthor,
    ) -> RunResult:
        graph = self._graphs.get_enabled_by_name(routine.graph_name)
        if graph is None:
            raise RoutineGraphUnresolvedError(routine.graph_name)
        slug = scope_slug if scope_slug is not None else ScopeSlug.parse(routine.default_scope_slug)
        scope = self._scope_registry.ensure(slug)
        if self._scopes.is_retired(scope.slug):
            raise ScopeRetiredError(scope.slug)

        baseline = self._finding_sets.newest_for_routine_scope(routine.name, scope.slug)
        effective_mode = mode
        downgraded = False
        if mode is RunMode.DELTA and baseline is None:
            effective_mode = RunMode.FULL
            downgraded = True
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
        title = f"{routine.name} run ({effective_mode.value})"

        pointer, chunk, pointer_at = prepare_mint(
            self._items,
            self._chunks,
            self._clock,
            RESERVED_HUB_SOURCE_NAME,
            graph=graph,
            default_model=routine.default_model,
            default_effort=routine.default_effort,
        )
        position = tail_position(self._chunks)
        item, promoted_id = self._items.create_with_chunk_and_promote(
            pointer=pointer,
            title=title,
            body=charge,
            author=author,
            routine_name=routine.name,
            scope_slug=scope.slug,
            run_mode=effective_mode.value,
            at=pointer_at,
            chunk=chunk,
            position=position,
        )
        return RunResult(
            item=item,
            chunk_id=chunk.chunk_id,
            promoted_id=promoted_id,
            effective_mode=effective_mode,
            downgraded=downgraded,
            baseline=baseline,
        )
