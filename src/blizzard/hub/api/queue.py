"""Queue and backlog routes — read, replace, and group.

The ``ready`` queue and ``not_ready`` list each rank independently
(``bzh:ranking-is-per-list``); controllers stay read-only and delegate writes to the
queue-shaping domain (``bzh:controller-read-only``). Backlog routes require
``Permission.QUEUE_REORDER`` even to read — an operator triage surface, not fleet-wide visibility."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Query

from blizzard.auth_core import Permission
from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.claim_pass_reason import ClaimPassReason
from blizzard.foundation.queue_policy import QueueMatchPolicy
from blizzard.foundation.roles import dto
from blizzard.hub.api import chunk_events, problems, problems_chunks
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.chunk_views import blocked_view
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.project_label_fields import project_label_fields
from blizzard.hub.api.project_params import QueryProject
from blizzard.hub.api.urgency_refs import urgency_ref
from blizzard.hub.domain.chunk.dependencies import derive_blocked_prerequisites
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import PRE_CLAIM_STATUSES, Chunk, ChunkFacts
from blizzard.hub.domain.chunk.ports.dependencies import IReadChunkDependenciesRepository
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.queue import QueueList
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.config.repositories import IReadRepositoryRecordRepository
from blizzard.hub.domain.graph.model import Graph, IReadGraphRepository
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.operations.claim_explanation import ClaimExplanation, explain_next_claim
from blizzard.hub.domain.operations.queue import (
    ChunkNotGroupable,
    ChunksInDifferentProjects,
    DuplicateReorderIds,
    FoldWouldCloseCycle,
    GroupService,
    MatchedEntry,
    NotInList,
    PassedEntry,
    PassedReason,
    QueueEntry,
    QueueService,
    RankedOrder,
    SelfAnchoredMove,
    entries_of,
    matched_candidates,
    queue_match_policy,
    select_matched_entry,
)
from blizzard.hub.domain.projects.model import IReadProjectRepository, Project, ProjectId
from blizzard.hub.domain.projects.targets import (
    ChunkTargets,
    TargetChunk,
    Targets,
    unoffered_without_targets,
    with_records,
)
from blizzard.hub.domain.runners.registration import RunnerCapability
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId
from blizzard.hub.domain.urgency.services import UrgencyReading
from blizzard.hub.events.broker import EventBroker
from blizzard.wire.chunk import WorkRefModel
from blizzard.wire.queue import (
    BacklogPageView,
    BacklogPeekEntry,
    BacklogPeekResponse,
    BacklogPositionRequest,
    BacklogReplaceRequest,
    ChunkGroupRequest,
    ChunkGroupResponse,
    NextClaimEntry,
    NextClaimPassedEntry,
    NextClaimResponse,
    NextClaimScenario,
    QueuePageView,
    QueuePeekEntry,
    QueuePeekRequest,
    QueuePeekResponse,
    QueuePositionRequest,
    QueueReplaceRequest,
    RepositoryTarget,
)
from blizzard.wire.runner import RunnerCapability as WireRunnerCapability

router = APIRouter(
    prefix="/api", tags=["queue"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _resolved(list_: QueueList, chunk_ids: Sequence[str], records: IReadChunkRecordRepository) -> list[Chunk]:
    """The chunks ``chunk_ids`` name, in the order named — one batched read. An id naming no chunk
    is refused as outside ``list_`` (:class:`NotInList`), the same ``409`` as one that exists
    elsewhere; a chunk that does not exist stands in no list. A repeated id resolves to the same
    chunk each time, so the domain still sees the repeat."""
    found = records.get_many(chunk_ids)
    for chunk_id in chunk_ids:
        if chunk_id not in found:
            raise NotInList(chunk_id, list_, None)
    return [found[chunk_id] for chunk_id in chunk_ids]


def _resolved_move(
    list_: QueueList, chunk_id: str, after_chunk_id: str | None, records: IReadChunkRecordRepository
) -> tuple[Chunk, Chunk | None]:
    """The chunk a move names and its anchor (``None``: the top), resolved as :func:`_resolved` does."""
    named = [chunk_id] if after_chunk_id is None else [chunk_id, after_chunk_id]
    chunk, *anchors = _resolved(list_, named, records)
    return chunk, anchors[0] if anchors else None


def _replace(
    list_: QueueList,
    chunk_ids: list[str],
    queue: QueueService,
    records: IReadChunkRecordRepository,
    events: EventBroker,
) -> RankedOrder:
    """Replace ``list_``'s order — the body ``PUT /api/queue`` and ``PUT /api/backlog`` share,
    differing only in which list they rank (``bzh:ranking-is-per-list``) — and return the order
    the domain wrote with the statuses it decided on."""
    try:
        ranked = queue.reorder(list_, _resolved(list_, chunk_ids, records))
    except DuplicateReorderIds as exc:
        raise ProblemHTTPException(partial(problems_chunks.duplicate_reorder_ids_problem, str(exc))) from exc
    except NotInList as exc:
        raise ProblemHTTPException(partial(problems_chunks.not_in_list_problem, str(exc))) from exc
    events.publish_queue_changed()
    return ranked


def _reposition(
    list_: QueueList,
    chunk_id: str,
    after_chunk_id: str | None,
    queue: QueueService,
    records: IReadChunkRecordRepository,
    events: EventBroker,
) -> RankedOrder:
    """Move one chunk within ``list_`` — the body ``POST /api/queue/position`` and
    ``POST /api/backlog/position`` share, differing only in which list they rank
    (``bzh:ranking-is-per-list``) — and return the order the move leaves with the statuses it
    decided on."""
    try:
        chunk, after = _resolved_move(list_, chunk_id, after_chunk_id, records)
        ranked = queue.reposition(list_, chunk, after)
    except SelfAnchoredMove as exc:
        raise ProblemHTTPException(partial(problems_chunks.self_anchored_move_problem, str(exc))) from exc
    except NotInList as exc:
        raise ProblemHTTPException(partial(problems_chunks.not_in_list_problem, str(exc))) from exc
    events.publish_queue_changed()
    return ranked


def _entry_view[Entry: (QueuePeekEntry, BacklogPeekEntry)](
    model: type[Entry],
    entry: QueueEntry,
    markings: Mapping[str, list[str]],
    projects: Mapping[ProjectId, Project],
    urgencies: Mapping[UrgencyId, Urgency],
) -> Entry:
    """One queue or backlog row at its whole-order position, labelled with its chunk's project and urgency."""
    chunk = entry.chunk
    project = projects.get(chunk.project_id) if chunk.project_id is not None else None
    return model(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        position=entry.position,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in chunk.work_refs],
        blocked=blocked_view(markings.get(chunk.chunk_id)),
        **project_label_fields(chunk.project_id, project),
        urgency=urgency_ref(chunk.urgency_id, urgencies),
    )


@dto
@dataclass(frozen=True)
class ReadyQueue:
    """The ready queue as a peek or a queue write renders it, in the order given. Position is the whole order's
    own index, kept when a project narrows the entries. ``markings`` and ``projects`` are
    resolved by :class:`QueueViewAssembler`, alongside ``entries``, so :attr:`view` stays a pure
    projection over already-fetched state rather than an I/O-performing property."""

    entries: list[QueueEntry]
    markings: dict[str, list[str]]
    projects: dict[ProjectId, Project]
    urgencies: dict[UrgencyId, Urgency]

    @property
    def view(self) -> QueuePeekResponse:
        return QueuePeekResponse(
            entries=[
                _entry_view(QueuePeekEntry, entry, self.markings, self.projects, self.urgencies)
                for entry in self.entries
            ]
        )


def _domain_capabilities(capabilities: Sequence[WireRunnerCapability]) -> tuple[RunnerCapability, ...]:
    """The matched peek's wire runner capabilities, converted field for field to the domain's."""
    return tuple(
        RunnerCapability(
            harness_id=c.harness_id,
            version=c.version,
            tiers=tuple(c.tiers),
            supported_models=tuple(c.supported_models),
            default=c.default,
            available=c.available,
        )
        for c in capabilities
    )


@dto
@dataclass(frozen=True)
class WalkReads:
    """The reads a matched walk judges ``candidates`` by — their blocked markings, movement facts, and graphs — each one
    plural bulk read (``bzh:bulk-reconstitution``), so the statement count stays flat as the ready order grows."""

    markings: dict[str, list[str]]
    facts: dict[str, ChunkFacts]
    graphs: dict[str, Graph]


@dto
@dataclass(frozen=True)
class MatchedPeek:
    """The matched fleet peek — at most one ready entry of a project the calling runner
    serves that it can both work (``EligibilityCheck``) and claim (existing
    blocked-dependency marking), resolved via the same plural bulk reads
    ``bzh:bulk-reconstitution`` names, so the statement count stays flat as the ready
    order grows. ``served`` is the runner's stored served set, never the request's."""

    entry: MatchedEntry | None
    project: Project | None
    urgencies: dict[UrgencyId, Urgency]
    targets: list[RepositoryTarget]

    @property
    def view(self) -> QueuePeekResponse:  # ast-grep-ignore: bzh:property-delegates
        if self.entry is None:
            return QueuePeekResponse(entries=[])
        chunk = self.entry.chunk
        return QueuePeekResponse(
            entries=[
                QueuePeekEntry(
                    chunk_id=chunk.chunk_id,
                    graph_id=chunk.graph_id,
                    position=self.entry.position,
                    work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in chunk.work_refs],
                    # A matched entry is, by construction, never blocked (`select_matched_entry`'s own
                    # policy check covers that dimension too before one is ever selected).
                    blocked=None,
                    **project_label_fields(chunk.project_id, self.project),
                    urgency=urgency_ref(chunk.urgency_id, self.urgencies),
                    repository_targets=self.targets,
                )
            ]
        )


_PASS_REASONS = {PassedReason.BLOCKED: ClaimPassReason.BLOCKED, PassedReason.INELIGIBLE: ClaimPassReason.INELIGIBLE}


def _next_claim_entry(
    chunk: Chunk, position: int, projects: Mapping[ProjectId, Project], urgencies: Mapping[UrgencyId, Urgency]
) -> NextClaimEntry:
    """One chunk a runner's walk reaches, at its claim-order position, labelled with its project and urgency."""
    project = projects.get(chunk.project_id) if chunk.project_id is not None else None
    return NextClaimEntry(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        position=position,
        **project_label_fields(chunk.project_id, project),
        urgency=urgency_ref(chunk.urgency_id, urgencies),
    )


def _next_claim_passed(
    entry: PassedEntry, projects: Mapping[ProjectId, Project], urgencies: Mapping[UrgencyId, Urgency]
) -> NextClaimPassedEntry:
    """One passed chunk with why, its reason mapped onto the wire's own vocabulary."""
    return NextClaimPassedEntry(
        **_next_claim_entry(entry.chunk, entry.position, projects, urgencies).model_dump(),
        reason=_PASS_REASONS[entry.reason],
        prerequisite=entry.prerequisite,
        prerequisite_count=entry.prerequisite_count,
    )


@dto
@dataclass(frozen=True)
class NextClaim:
    """One runner's next claim, explained (:func:`explain_next_claim`) over every served ready chunk — the candidates a
    pass-over peek reads, and a superset of a hold peek's — so one read feeds every walk. ``explanation`` assumes the
    runner prepares at the repository targets a peek names; ``without_targets`` is the same walk for a runner that
    prepares at none, skipping the chunks it is never offered, over the same reads and one read of the served chunks'
    targets. No claim bar is applied."""

    explanation: ClaimExplanation
    without_targets: ClaimExplanation
    projects: dict[ProjectId, Project]
    urgencies: dict[UrgencyId, Urgency]

    @property
    def view(self) -> NextClaimResponse:
        projects, urgencies = self.projects, self.urgencies
        own = _next_claim_scenario(self.explanation, projects, urgencies)
        return NextClaimResponse(
            next=own.next,
            passed=own.passed,
            passed_total=own.passed_total,
            unserved=own.unserved,
            if_holding=own.if_holding,
            held_at=own.held_at,
            without_repository_targets=_next_claim_scenario(self.without_targets, projects, urgencies),
        )


def _next_claim_scenario(
    explained: ClaimExplanation, projects: Mapping[ProjectId, Project], urgencies: Mapping[UrgencyId, Urgency]
) -> NextClaimScenario:
    """One explanation as the wire's scenario — the fields the response carries for its own, plus ``excluded``."""
    return NextClaimScenario(
        next=None
        if explained.next is None
        else _next_claim_entry(explained.next.chunk, explained.next.position, projects, urgencies),
        passed=[_next_claim_passed(entry, projects, urgencies) for entry in explained.passed],
        passed_total=explained.passed_total,
        unserved=explained.unserved,
        if_holding=None
        if explained.if_holding is None
        else _next_claim_entry(explained.if_holding.chunk, explained.if_holding.position, projects, urgencies),
        held_at=[_next_claim_passed(entry, projects, urgencies) for entry in explained.held_at],
        excluded=explained.excluded,
    )


class QueueViewAssembler:
    """Builds every queue and backlog view, owning the ports they render from — the chunks' facts and standing edges,
    the per-list order, the projects, urgencies and graphs rows are labelled and judged by, and the repository targets
    an offered chunk names. Callers hand it the live statuses they already read; no caller reaches a port."""

    def __init__(
        self,
        facts: IReadChunkFactsRepository,
        dependencies: IReadChunkDependenciesRepository,
        order: QueueService,
        projects: IReadProjectRepository,
        urgencies: UrgencyReading,
        graphs: IReadGraphRepository,
        targets: ChunkTargets,
        repositories: IReadRepositoryRecordRepository,
    ) -> None:
        self._facts = facts
        self._dependencies = dependencies
        self._order = order
        self._projects = projects
        self._urgencies = urgencies
        self._graphs = graphs
        self._targets = targets
        self._repositories = repositories

    def ready(self, statuses: Mapping[str, ChunkStatus], *, project: ProjectId | None = None) -> ReadyQueue:
        """The ready queue in claim order (urgency before rank)."""
        return self.ready_ranked(self._order.claim_ordered_ready(statuses=statuses), statuses, project=project)

    def ready_ranked(
        self,
        ordered: Sequence[Chunk],
        statuses: Mapping[str, ChunkStatus],
        *,
        project: ProjectId | None = None,
    ) -> ReadyQueue:
        """The queue as ``ordered`` ranks it — a write's own decided order — decorated with
        ``statuses``' blocked markings."""
        entries = entries_of(ordered, None if project is None else {project})
        return ReadyQueue(
            entries,
            self._blocked_markings(statuses, [entry.chunk.chunk_id for entry in entries]),
            self._projects_of(entries),
            self._urgencies.by_id(),
        )

    def backlog_ranked(self, ordered: Sequence[Chunk], statuses: Mapping[str, ChunkStatus]) -> Backlog:
        """The backlog as ``ordered`` ranks it — a write's own decided order — decorated with
        ``statuses``' blocked markings."""
        entries = entries_of(ordered)
        return Backlog(
            entries,
            self._blocked_markings(statuses, [entry.chunk.chunk_id for entry in entries]),
            self._projects_of(entries),
            self._urgencies.by_id(),
        )

    def ready_page(
        self, statuses: Mapping[str, ChunkStatus], *, cursor: str | None, limit: int, project: ProjectId | None
    ) -> QueuePageView:
        """One keyset page of the ready queue; :class:`MalformedCursor` on an unreadable ``cursor``."""
        page = self._order.page(QueueList.READY, statuses=statuses, cursor=cursor, limit=limit, project=project)
        markings = self._blocked_markings(statuses, [entry.chunk.chunk_id for entry in page.entries])
        projects, urgencies = self._projects_of(page.entries), self._urgencies.by_id()
        return QueuePageView(
            entries=[_entry_view(QueuePeekEntry, entry, markings, projects, urgencies) for entry in page.entries],
            next_cursor=page.next_cursor,
        )

    def backlog_page(
        self, statuses: Mapping[str, ChunkStatus], *, cursor: str | None, limit: int, project: ProjectId | None
    ) -> BacklogPageView:
        """One keyset page of the backlog; :class:`MalformedCursor` on an unreadable ``cursor``."""
        page = self._order.page(QueueList.NOT_READY, statuses=statuses, cursor=cursor, limit=limit, project=project)
        markings = self._blocked_markings(statuses, [entry.chunk.chunk_id for entry in page.entries])
        projects, urgencies = self._projects_of(page.entries), self._urgencies.by_id()
        return BacklogPageView(
            entries=[_entry_view(BacklogPeekEntry, entry, markings, projects, urgencies) for entry in page.entries],
            next_cursor=page.next_cursor,
        )

    def matched_peek(
        self,
        statuses: Mapping[str, ChunkStatus],
        request: QueuePeekRequest,
        *,
        served: Collection[str],
    ) -> MatchedPeek:
        ordered = self._order.claim_ordered_ready(statuses=statuses)
        policy = queue_match_policy(request.policy)
        excluded: frozenset[str] = (
            frozenset()
            if request.repository_targets
            else unoffered_without_targets(self._served_targets(ordered, served))
        )
        per_project = request.projects is not None
        walk = self._walk_reads(
            statuses,
            matched_candidates(ordered, served=served, excluded=excluded, policy=policy, hold_per_project=per_project),
        )
        entry = select_matched_entry(
            ordered,
            served=served,
            graphs=walk.graphs,
            facts=walk.facts,
            capabilities=_domain_capabilities(request.capabilities),
            blocked=walk.markings,
            policy=policy,
            hold_per_project=per_project,
            excluded=excluded,
        )
        if entry is None:
            return MatchedPeek(None, None, {}, [])
        chunk = entry.chunk
        return MatchedPeek(
            entry,
            self._projects.get(chunk.project_id) if chunk.project_id is not None else None,
            self._urgencies.by_id(),
            self._repository_targets(chunk),
        )

    def next_claim(
        self,
        statuses: Mapping[str, ChunkStatus],
        *,
        served: Collection[str],
        capabilities: Sequence[RunnerCapability],
    ) -> NextClaim:
        """One runner's next claim; ``served`` and ``capabilities`` are what the hub holds of the runner."""
        chunks = self._order.claim_ordered_ready(statuses=statuses)
        walk = self._walk_reads(
            statuses,
            matched_candidates(
                chunks,
                served=served,
                excluded=frozenset(),
                policy=QueueMatchPolicy.PASS_OVER,
                hold_per_project=False,
            ),
        )
        explain = partial(
            explain_next_claim,
            chunks,
            served=served,
            graphs=walk.graphs,
            facts=walk.facts,
            capabilities=capabilities,
            blocked=walk.markings,
        )
        explanation = explain()
        without_targets = explain(excluded=unoffered_without_targets(self._served_targets(chunks, served)))
        shown = [
            entry.chunk
            for explained in (explanation, without_targets)
            for entry in (explained.next, explained.if_holding, *explained.passed, *explained.held_at)
            if entry is not None
        ]
        project_ids = sorted({chunk.project_id for chunk in shown if chunk.project_id is not None})
        return NextClaim(explanation, without_targets, self._projects.get_many(project_ids), self._urgencies.by_id())

    def _blocked_markings(
        self, statuses: Mapping[str, ChunkStatus], dependent_ids: Sequence[str]
    ) -> dict[str, list[str]]:
        """Mark only the displayed dependents.
        The live status map still resolves on-page and off-page prerequisites; a finished
        prerequisite absent from it is resolved by id (``bzh:bulk-reconstitution``)."""
        pre_claim = [chunk_id for chunk_id in dependent_ids if statuses[chunk_id] in PRE_CLAIM_STATUSES]
        edges = self._dependencies.standing_edges_for_dependents(pre_claim)
        absent = sorted({e.prerequisite_chunk_id for e in edges} - statuses.keys())
        resolved = {chunk_id: facts.status() for chunk_id, facts in self._facts.status_facts_for(absent).items()}
        return derive_blocked_prerequisites(edges, {**statuses, **resolved})

    def _projects_of(self, entries: Iterable[QueueEntry]) -> dict[ProjectId, Project]:
        """The projects ``entries``' chunks belong to — one batched read, narrowed to the entries."""
        return self._projects.get_many(sorted({e.chunk.project_id for e in entries if e.chunk.project_id is not None}))

    def _walk_reads(self, statuses: Mapping[str, ChunkStatus], candidates: Sequence[Chunk]) -> WalkReads:
        chunk_ids = [chunk.chunk_id for chunk in candidates]
        return WalkReads(
            self._blocked_markings(statuses, chunk_ids),
            self._facts.movement_facts_for(chunk_ids),
            self._graphs.get_many(sorted({chunk.graph_id for chunk in candidates})),
        )

    def _served_targets(self, chunks: Sequence[Chunk], served: Collection[str]) -> Mapping[str, Targets]:
        """Each served chunk's effective targets, read once over every one of them (``bzh:bulk-reconstitution``) and
        never captured."""
        return self._targets.effective_for(
            [TargetChunk(chunk.chunk_id, chunk.project_id) for chunk in chunks if chunk.project_id in served]
        )

    def _repository_targets(self, chunk: Chunk) -> list[RepositoryTarget]:
        """The offered chunk's effective targets, each with the ``owner/repo`` a runner matches a worktree's origin
        against (:func:`with_records`)."""
        targets = self._targets.effective(TargetChunk(chunk.chunk_id, chunk.project_id))
        records = self._repositories.get_many(sorted(targets)) if targets else {}
        return [
            RepositoryTarget(
                repository_id=target.repository_id,
                repository_slug=record.slug,
                owner=record.fields.owner,
                repo=record.fields.repo,
                base_branch=target.base_branch,
                inherited=target.inherited,
            )
            for target, record in with_records(targets, records)
        ]


@router.get("/queue", response_model=QueuePageView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def get_queue(
    queues: FromDishka[QueueViewAssembler],
    facts: FromDishka[IReadChunkFactsRepository],
    project: QueryProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> QueuePageView:
    """The hub-ordered ready queue, read-only and keyset-paginated
    — honours reorder/replace + grouping. ``?project=`` narrows it to that project's
    chunks at their whole-queue positions, never reranked; without it, the whole tenant's."""
    statuses = facts.load_live_statuses()
    try:
        return queues.ready_page(statuses, cursor=cursor, limit=limit, project=project)
    except MalformedCursor as exc:
        raise ProblemHTTPException(partial(problems.malformed_cursor_problem, exc)) from exc


@router.put("/queue", response_model=QueuePeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))])
@inject_sync
def replace_queue(
    request: QueueReplaceRequest,
    queues: FromDishka[QueueViewAssembler],
    order: FromDishka[QueueService],
    records: FromDishka[IReadChunkRecordRepository],
    events: FromDishka[EventBroker],
) -> QueuePeekResponse:
    """Idempotent whole-order replacement of the ready queue.

    Resolves every named id against the current ready set: ``409`` names the first id
    that is not ready, ``422`` a duplicate id. An unnamed ready chunk keeps its relative
    order, appended after the named ones."""
    ranked = _replace(QueueList.READY, request.chunk_ids, order, records, events)
    # A position write changes no chunk's status, so the statuses the unit decided on still
    # decorate the order it wrote — no second derivation.
    return queues.ready_ranked(ranked.ordered, ranked.statuses).view


@router.post(
    "/queue/position", response_model=QueuePeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))]
)
@inject_sync
def reposition_queue(
    request: QueuePositionRequest,
    queues: FromDishka[QueueViewAssembler],
    order: FromDishka[QueueService],
    records: FromDishka[IReadChunkRecordRepository],
    events: FromDishka[EventBroker],
) -> QueuePeekResponse:
    """Single-chunk fractional reorder.

    Resolves both ids against the current ready set: ``409`` names either one if it is
    not ready, ``422`` rejects a self-anchor. ``after_chunk_id=null`` moves the chunk to
    the top of the queue."""
    ranked = _reposition(QueueList.READY, request.chunk_id, request.after_chunk_id, order, records, events)
    # A position write changes no chunk's status, so the statuses the unit decided on still
    # decorate the order it wrote — no second derivation.
    return queues.ready_ranked(ranked.ordered, ranked.statuses).view


@dto
@dataclass(frozen=True)
class Backlog:
    """The hub-ordered ``not_ready`` list as every peek renders it — position is the
    order itself. ``markings`` and ``projects`` are resolved by :class:`QueueViewAssembler`, alongside
    ``entries``, so :attr:`view` stays a pure projection over already-fetched state rather
    than an I/O-performing property."""

    entries: list[QueueEntry]
    markings: dict[str, list[str]]
    projects: dict[ProjectId, Project]
    urgencies: dict[UrgencyId, Urgency]

    @property
    def view(self) -> BacklogPeekResponse:
        return BacklogPeekResponse(
            entries=[
                _entry_view(BacklogPeekEntry, entry, self.markings, self.projects, self.urgencies)
                for entry in self.entries
            ]
        )


@router.get("/backlog", response_model=BacklogPageView, dependencies=[Depends(require(Permission.QUEUE_REORDER))])
@inject_sync
def get_backlog(
    queues: FromDishka[QueueViewAssembler],
    facts: FromDishka[IReadChunkFactsRepository],
    project: QueryProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> BacklogPageView:
    """The hub-ordered ``not_ready`` list, read-only and keyset-paginated
    — an operator triage surface, requiring ``queue:reorder`` not ``fleet:view``.
    ``?project=`` narrows it to that project's chunks at their whole-list positions, never
    reranked; without it, the whole tenant's."""
    statuses = facts.load_live_statuses()
    try:
        return queues.backlog_page(statuses, cursor=cursor, limit=limit, project=project)
    except MalformedCursor as exc:
        raise ProblemHTTPException(partial(problems.malformed_cursor_problem, exc)) from exc


@router.put("/backlog", response_model=BacklogPeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))])
@inject_sync
def replace_backlog(
    request: BacklogReplaceRequest,
    queues: FromDishka[QueueViewAssembler],
    order: FromDishka[QueueService],
    records: FromDishka[IReadChunkRecordRepository],
    events: FromDishka[EventBroker],
) -> BacklogPeekResponse:
    """Idempotent whole-order replacement of the backlog.

    Resolves every named id against the current ``not_ready`` set: ``409`` names the
    first id that is not ``not_ready``, ``422`` a duplicate id. An unnamed chunk keeps
    its relative order, appended after the named ones."""
    ranked = _replace(QueueList.NOT_READY, request.chunk_ids, order, records, events)
    # A position write changes no chunk's status, so the statuses the unit decided on still
    # decorate the order it wrote — no second derivation.
    return queues.backlog_ranked(ranked.ordered, ranked.statuses).view


@router.post(
    "/backlog/position", response_model=BacklogPeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))]
)
@inject_sync
def reposition_backlog(
    request: BacklogPositionRequest,
    queues: FromDishka[QueueViewAssembler],
    order: FromDishka[QueueService],
    records: FromDishka[IReadChunkRecordRepository],
    events: FromDishka[EventBroker],
) -> BacklogPeekResponse:
    """Single-chunk fractional reorder within the backlog.

    Resolves both ids against the current ``not_ready`` set: ``409`` names either one if
    it is not ``not_ready``, ``422`` rejects a self-anchor. ``after_chunk_id=null`` moves
    the chunk to the top of the backlog."""
    ranked = _reposition(QueueList.NOT_READY, request.chunk_id, request.after_chunk_id, order, records, events)
    # A position write changes no chunk's status, so the statuses the unit decided on still
    # decorate the order it wrote — no second derivation.
    return queues.backlog_ranked(ranked.ordered, ranked.statuses).view


@router.post(
    "/chunks/{chunk_id}/group",
    response_model=ChunkGroupResponse,
    dependencies=[Depends(require(Permission.QUEUE_REORDER))],
)
@inject_sync
def group_chunks(
    chunk_id: str,
    request: ChunkGroupRequest,
    groups: FromDishka[GroupService],
    frames: FromDishka[ChunkFrameLoader],
    events: FromDishka[EventBroker],
) -> object:
    """Merge unacquired chunks into ``chunk_id``. Accepts ``not_ready`` and ``ready``
    participants alike; 409 names the first chunk a runner holds, or one
    already finished, or one of another project than the survivor's. The survivor also
    absorbs each folded chunk's standing dependency edges; 409 refuses a fold that would
    close a cycle."""
    before = chunk_events.ChunkChanged.before(frames, chunk_id)
    try:
        result = groups.group(chunk_id, request.merge_chunk_ids)
    except ChunkNotFound as exc:
        raise ProblemHTTPException(partial(problems.chunk_disappeared_problem, exc)) from exc
    except ChunkNotGroupable as exc:
        raise ProblemHTTPException(partial(problems_chunks.chunk_not_groupable_problem, str(exc))) from exc
    except ChunksInDifferentProjects as exc:
        raise ProblemHTTPException(partial(problems_chunks.chunks_in_different_projects_problem, str(exc))) from exc
    except FoldWouldCloseCycle as exc:
        raise ProblemHTTPException(partial(problems_chunks.fold_would_close_cycle_problem, str(exc))) from exc
    # The survivor's status comes from the group result, never a ``"ready"`` constant:
    # grouping backlog chunks leaves a backlog survivor.
    survivor = result.survivor
    events.publish_queue_changed()
    key = f"chunk_grouped:{result.grouped_id}" if result.grouped_id is not None else None
    chunk_events.ChunkChanged.of(frames, survivor.chunk_id, prev_status=before.prev_status).publish(
        cause="grouped", status=result.status.value, key=key
    )
    return ChunkGroupResponse(
        chunk_id=survivor.chunk_id,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in survivor.work_refs],
        merged_chunk_ids=[m for m in request.merge_chunk_ids if m != survivor.chunk_id],
    )
