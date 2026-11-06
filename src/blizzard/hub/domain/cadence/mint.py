"""Minting a due errand — its planning inputs, read in bulk, and the unit of work that mints its run.

An errand is planned as a hand-started run is: the garden's pure :func:`~blizzard.hub.domain.garden.runs.run.plan_run`
refuses in the same order and settles the mode the same way, over inputs read for a whole due set at once
(``bzh:bulk-reconstitution``). The mint itself is one transaction (``bzh:unit-of-work``): it locks the errand, and the
ranking locks a promote takes when its chore promotes, re-reads the errand's last run under them
(``bzh:write-reestablishes-read``), and only then writes the run and, for a promoting chore, its landing at the tail of
the ready queue."""

from __future__ import annotations

from collections.abc import Collection, Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.errands import Errand, ErrandKey, ErrandRun
from blizzard.hub.domain.cadence.model import AttentionFacts
from blizzard.hub.domain.cadence.ports import IReadErrandRunsRepository, errand_lock
from blizzard.hub.domain.chunk.model import Chunk, ErrandMark, HubWorkItem, WorkItemAuthor
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK, READY_RANKING_LOCK, IWriteChunkQueueRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.garden.findings.model import FindingSet, IReadFindingSetRepository
from blizzard.hub.domain.garden.routines import Routine
from blizzard.hub.domain.garden.runs.run import RunPlan, plan_run
from blizzard.hub.domain.garden.scopes import Scope
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.operations.promote import ready_members
from blizzard.hub.domain.operations.queue import (
    TAIL_PLACEMENT,
    QueueList,
    read_ranking,
    resolve_landing,
    write_positions,
)
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.work_items.editing import prepare_mint
from blizzard.hub.domain.work_items.ports import IWriteWorkItemRepository


@domain_model
@dataclass(frozen=True)
class ErrandInputs:
    """Everything one errand's run is planned from, already loaded: its routine and scope as they stand, the
    routine's linked scopes, its graph if one resolves, and the pair's delta baseline if one is recorded."""

    routine: Routine
    scope: Scope
    linked: Collection[str]
    graph: Graph | None
    baseline: FindingSet | None


def plan_errand(errand: Errand, inputs: ErrandInputs) -> RunPlan:
    """Plan ``errand``'s run as a hand-started run is planned, its chore's mode requested and its note the charge's
    *This run*; the garden's refusals raise in the garden's order."""
    return plan_run(
        inputs.routine,
        routine_retired=inputs.routine.retired,
        graph=inputs.graph,
        scope=inputs.scope,
        scope_retired=inputs.scope.retired,
        linked=inputs.linked,
        requested_mode=errand.chore.spec.mode,
        baseline=inputs.baseline,
        note=errand.chore.spec.note,
    )


class ErrandPlanning:
    """Read the planning inputs of a set of due errands in bulk: one graph read per graph name and one baseline read
    per routine the set names, never a read per errand. The routines, scopes, and links are the facts the errands
    were resolved against."""

    def __init__(self, *, graphs: IReadGraphRepository, finding_sets: IReadFindingSetRepository) -> None:
        self._graphs = graphs
        self._finding_sets = finding_sets

    def inputs(self, due: Sequence[tuple[Errand, AttentionFacts]]) -> dict[ErrandKey, ErrandInputs]:
        """Each of ``due``'s errands, paired with the facts of its cadence's project, keyed by errand; one whose
        routine or scope those facts do not hold has no entry."""
        resolved: list[tuple[Errand, Routine, Scope, AttentionFacts]] = []
        for errand, facts in due:
            routine = facts.routines.get(errand.chore.spec.routine_slug)
            scope = facts.scopes.get(errand.scope_slug)
            if routine is not None and scope is not None:
                resolved.append((errand, routine, scope, facts))
        graphs = {
            name: self._graphs.get_enabled_by_name(name)
            for name in sorted({routine.graph_name for _, routine, _, _ in resolved})
        }
        baselines: dict[tuple[str, str], Mapping[str, FindingSet]] = {
            (project_id, slug): {
                found.scope_slug: found for found in self._finding_sets.newest_by_scope_for_routine(project_id, slug)
            }
            for project_id, slug in sorted({(routine.project_id, routine.slug) for _, routine, _, _ in resolved})
        }
        return {
            errand.key: ErrandInputs(
                routine=routine,
                scope=scope,
                linked=facts.linked.get(routine.routine_id, ()),
                graph=graphs[routine.graph_name],
                baseline=baselines[(routine.project_id, routine.slug)].get(scope.slug),
            )
            for errand, routine, scope, facts in resolved
        }


def mint_lock_keys(key: ErrandKey, *, promote: bool) -> list[tuple[str, str]]:
    """The keyed locks an errand's mint takes: its errand's, and for a promoting chore both lists' ranking locks, as
    the queue's own promote takes them. The store takes them in canonical order: errand, then ranking."""
    return [errand_lock(key), *((READY_RANKING_LOCK, BACKLOG_RANKING_LOCK) if promote else ())]


def run_moved(judged: ErrandRun | None, current: ErrandRun | None) -> bool:
    """Whether the errand's last run, re-read under its lock as ``current``, is no longer the ``judged`` one, or is
    unfinished — another mint got there first."""
    if current is None:
        return judged is not None
    return judged is None or current.work_item_id != judged.work_item_id or current.run.unfinished()


@domain_model
@dataclass(frozen=True)
class MintedErrand:
    """What one committed mint changed: the run's work item and chunk, how its mode settled, and whether it landed
    promoted."""

    errand: Errand
    item: HubWorkItem
    chunk: Chunk
    plan: RunPlan
    promoted: bool


class CadenceMint:
    """Mint one due errand's run in a unit of work of its own."""

    def __init__(
        self,
        *,
        items: IWriteWorkItemRepository,
        work_refs: IReadChunkWorkRefsRepository,
        runs: IReadErrandRunsRepository,
        record: IReadChunkRecordRepository,
        queue: IWriteChunkQueueRepository,
        facts: IReadChunkFactsRepository,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._items = items
        self._work_refs = work_refs
        self._runs = runs
        self._record = record
        self._queue = queue
        self._facts = facts
        self._transactions = transactions
        self._clock = clock

    def mint(self, errand: Errand, *, judged: ErrandRun | None, inputs: ErrandInputs) -> MintedErrand | None:
        """Mint ``errand``'s run, planned from ``inputs``, unless its last run moved since the pass ``judged`` it —
        then ``None``, with nothing written. A planning refusal raises before anything is allocated; the work ref is
        allocated before the transaction, so a crash between them burns one ref and nothing more.

        One lock declaration takes the errand's lock and, for a promoting chore, both lists' ranking locks, beside
        the fresh chunk's id, which names no row yet and so contends with nothing but is what the promote write
        requires the unit to lock."""
        plan = plan_errand(errand, inputs)
        routine = inputs.routine
        pointer, chunk, at = prepare_mint(
            self._items,
            self._work_refs,
            self._clock,
            RESERVED_HUB_SOURCE_NAME,
            graph=plan.graph,
            project_id=ProjectId(routine.project_id),
            default_model=routine.default_model,
            default_effort=routine.default_effort,
            default_harnesses=routine.default_harnesses,
        )
        promote = errand.chore.spec.promote
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(
                chunks=[chunk.chunk_id] if promote else [], keys=mint_lock_keys(errand.key, promote=promote)
            )
            if run_moved(judged, self._runs.last_runs([errand.key], transaction=locked).get(errand.key)):
                return None
            item = self._items.create_run_with_chunk(
                pointer=pointer,
                title=plan.title,
                body=plan.charge,
                author=WorkItemAuthor.cadence(errand.cadence_id, errand.chore.chore_id),
                project_id=routine.project_id,
                routine_name=routine.slug,
                scope_slug=inputs.scope.slug,
                run_mode=plan.effective_mode.value,
                at=at,
                chunk=chunk,
                errand=ErrandMark(cadence_id=errand.cadence_id, chore_id=errand.chore.chore_id),
                transaction=locked,
            )
            if promote:
                self._land(chunk, locked)
        return MintedErrand(errand=errand, item=item, chunk=chunk, plan=plan, promoted=promote)

    def _land(self, chunk: Chunk, locked: LockedTransaction) -> None:
        """Promote the fresh ``chunk`` to the ready queue's tail as the queue's own promote lands one."""
        statuses = self._facts.load_live_statuses(transaction=locked)
        candidates = ready_members(self._record, self._facts, statuses=statuses, transaction=locked)
        landing = resolve_landing(
            read_ranking(self._queue, candidates, transaction=locked),
            QueueList.READY,
            candidates,
            chunk_id=chunk.chunk_id,
            placement=TAIL_PLACEMENT,
            statuses=statuses,
        )
        now = self._clock.now()
        if landing.renormalized:
            write_positions(self._queue, QueueList.READY, landing.renormalized, at=now, transaction=locked)
        self._queue.record_promote_with_position(chunk.chunk_id, position=landing.position, at=now, transaction=locked)
