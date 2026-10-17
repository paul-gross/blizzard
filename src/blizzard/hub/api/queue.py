"""Queue and backlog routes — read, replace, and group.

The ``ready`` queue and ``not_ready`` list each rank independently
(``bzh:ranking-is-per-list``); controllers stay read-only and delegate writes to the
queue-shaping domain (``bzh:controller-read-only``). Backlog routes require
``Permission.QUEUE_REORDER`` even to read — an operator triage surface, not fleet-wide visibility."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.chunk_status import PRE_CLAIM_STATUSES, ChunkStatus
from blizzard.foundation.roles import dto
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_views import blocked_view
from blizzard.hub.api.deps import Services
from blizzard.hub.api.project_label_fields import project_label_fields
from blizzard.hub.api.project_params import QueryProject
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.chunk.dependencies import derive_blocked_prerequisites
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.kernel.pagination import DEFAULT_LIMIT, MAX_LIMIT, MalformedCursor
from blizzard.hub.domain.operations.queue import (
    ChunkNotGroupable,
    ChunksInDifferentProjects,
    DuplicateReorderIds,
    FoldWouldCloseCycle,
    MatchedEntry,
    NotInList,
    QueueEntry,
    QueueList,
    QueueMatchPolicy,
    QueuePage,
    SelfAnchoredMove,
    entries_of,
    select_matched_entry,
)
from blizzard.hub.domain.projects.model import Project, ProjectId
from blizzard.hub.domain.runners.registration import RunnerCapability
from blizzard.wire.chunk import WorkRefModel
from blizzard.wire.queue import (
    BacklogPageView,
    BacklogPeekEntry,
    BacklogPeekResponse,
    BacklogPositionRequest,
    BacklogReplaceRequest,
    ChunkGroupRequest,
    ChunkGroupResponse,
    QueuePageView,
    QueuePeekEntry,
    QueuePeekRequest,
    QueuePeekResponse,
    QueuePositionRequest,
    QueueReplaceRequest,
)
from blizzard.wire.runner import RunnerCapability as WireRunnerCapability

router = APIRouter(
    prefix="/api", tags=["queue"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _blocked_markings(
    services: TenantServices, statuses: Mapping[str, ChunkStatus], dependent_ids: Sequence[str] | None = None
) -> dict[str, list[str]]:
    """Mark only the displayed dependents (or the whole live order for peeks and writes).
    The live status map still resolves on-page and off-page prerequisites; a finished
    prerequisite absent from it is resolved by id (``bzh:bulk-reconstitution``)."""
    candidates = dependent_ids if dependent_ids is not None else statuses
    pre_claim = [chunk_id for chunk_id in candidates if statuses[chunk_id] in PRE_CLAIM_STATUSES]
    edges = services.chunks.dependencies.standing_edges_for_dependents(pre_claim)
    absent = sorted({e.prerequisite_chunk_id for e in edges} - statuses.keys())
    resolved = {chunk_id: facts.status() for chunk_id, facts in services.chunks.facts.status_facts_for(absent).items()}
    return derive_blocked_prerequisites(edges, {**statuses, **resolved})


def _resolved(
    list_: QueueList, chunk_ids: Sequence[str], services: TenantServices, statuses: Mapping[str, ChunkStatus]
) -> list[Chunk]:
    """The chunks ``chunk_ids`` name, in the order named — one batched read. An id naming no chunk
    is refused as outside ``list_`` (:class:`NotInList`), the same ``409`` as one that exists
    elsewhere. A repeated id resolves to the same chunk each time, so the domain still sees the repeat."""
    found = services.chunks.record.get_many(chunk_ids)
    for chunk_id in chunk_ids:
        if chunk_id not in found:
            raise NotInList.of(chunk_id, list_, statuses)
    return [found[chunk_id] for chunk_id in chunk_ids]


def _resolved_move(
    list_: QueueList,
    chunk_id: str,
    after_chunk_id: str | None,
    services: TenantServices,
    statuses: Mapping[str, ChunkStatus],
) -> tuple[Chunk, Chunk | None]:
    """The chunk a move names and its anchor (``None``: the top), resolved as :func:`_resolved` does."""
    named = [chunk_id] if after_chunk_id is None else [chunk_id, after_chunk_id]
    chunk, *anchors = _resolved(list_, named, services, statuses)
    return chunk, anchors[0] if anchors else None


def _replace(
    list_: QueueList, chunk_ids: list[str], services: TenantServices, statuses: Mapping[str, ChunkStatus]
) -> list[Chunk]:
    """Replace ``list_``'s order — the body ``PUT /api/queue`` and ``PUT /api/backlog`` share,
    differing only in which list they rank (``bzh:ranking-is-per-list``). ``statuses`` is the
    caller's one-per-request derivation, threaded through."""
    try:
        ordered = services.queue.reorder(list_, _resolved(list_, chunk_ids, services, statuses), statuses=statuses)
    except DuplicateReorderIds as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except NotInList as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    services.events.publish_queue_changed()
    return ordered


def _reposition(
    list_: QueueList,
    chunk_id: str,
    after_chunk_id: str | None,
    services: TenantServices,
    statuses: Mapping[str, ChunkStatus],
) -> None:
    """Move one chunk within ``list_`` — the body ``POST /api/queue/position`` and
    ``POST /api/backlog/position`` share, differing only in which list they rank
    (``bzh:ranking-is-per-list``). ``statuses`` is the caller's one-per-request derivation,
    threaded through."""
    try:
        chunk, after = _resolved_move(list_, chunk_id, after_chunk_id, services, statuses)
        services.queue.move(list_, chunk, after, statuses=statuses)
    except SelfAnchoredMove as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=str(exc)) from exc
    except NotInList as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    services.events.publish_queue_changed()


def _projects_of(services: TenantServices, entries: Iterable[QueueEntry]) -> dict[ProjectId, Project]:
    """The projects ``entries``' chunks belong to — one batched read, narrowed to the entries."""
    return services.projects.get_many(sorted({e.chunk.project_id for e in entries if e.chunk.project_id is not None}))


def _entry_view[Entry: (QueuePeekEntry, BacklogPeekEntry)](
    model: type[Entry], entry: QueueEntry, markings: Mapping[str, list[str]], projects: Mapping[ProjectId, Project]
) -> Entry:
    """One queue or backlog row at its whole-order position, labelled with its chunk's project."""
    chunk = entry.chunk
    project = projects.get(chunk.project_id) if chunk.project_id is not None else None
    return model(
        chunk_id=chunk.chunk_id,
        graph_id=chunk.graph_id,
        position=entry.position,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in chunk.work_refs],
        blocked=blocked_view(markings.get(chunk.chunk_id)),
        **project_label_fields(chunk.project_id, project),
    )


@dto
@dataclass(frozen=True)
class ReadyQueue:
    """The hub-ordered ready queue as every peek renders it — position is the whole order's
    own index, kept when ``project`` narrows the entries. ``markings`` and ``projects`` are
    resolved once in :meth:`of`, alongside ``entries``, so :attr:`view` stays a pure
    projection over already-fetched state rather than an I/O-performing property."""

    entries: list[QueueEntry]
    markings: dict[str, list[str]]
    projects: dict[ProjectId, Project]

    @classmethod
    def of(
        cls, services: TenantServices, statuses: Mapping[str, ChunkStatus], *, project: ProjectId | None = None
    ) -> ReadyQueue:
        entries = entries_of(services.queue.ordered_ready(statuses=statuses), None if project is None else {project})
        return cls(entries, _blocked_markings(services, statuses), _projects_of(services, entries))

    @property
    def view(self) -> QueuePeekResponse:
        return QueuePeekResponse(
            entries=[_entry_view(QueuePeekEntry, entry, self.markings, self.projects) for entry in self.entries]
        )


def _domain_capabilities(capabilities: Sequence[WireRunnerCapability]) -> tuple[RunnerCapability, ...]:
    """The wire->domain conversion :func:`~blizzard.hub.api.fleet.register_runner`
    already applies to a runner's registration snapshot — the matched peek's own request
    carries the same shape, so it is converted the same way."""
    return tuple(
        RunnerCapability(
            harness_id=c.harness_id, version=c.version, tiers=tuple(c.tiers), default=c.default, available=c.available
        )
        for c in capabilities
    )


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

    @classmethod
    def of(
        cls,
        services: TenantServices,
        statuses: Mapping[str, ChunkStatus],
        request: QueuePeekRequest,
        *,
        served: Collection[str],
    ) -> MatchedPeek:
        chunks = services.queue.ordered_ready(statuses=statuses)
        policy = QueueMatchPolicy.of(request.policy)
        served_chunks = [chunk for chunk in chunks if chunk.project_id in served]
        candidates = served_chunks[:1] if policy is QueueMatchPolicy.HOLD else served_chunks
        markings = _blocked_markings(services, statuses, [chunk.chunk_id for chunk in candidates])
        facts = services.chunks.facts.movement_facts_for([chunk.chunk_id for chunk in candidates])
        graph_ids = sorted({chunk.graph_id for chunk in candidates})
        graphs = services.graphs.get_many(graph_ids)
        entry = select_matched_entry(
            chunks,
            served=served,
            graphs=graphs,
            facts=facts,
            capabilities=_domain_capabilities(request.capabilities),
            blocked=markings,
            policy=policy,
        )
        project_id = entry.chunk.project_id if entry is not None else None
        return cls(entry, services.projects.get(project_id) if project_id is not None else None)

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
                )
            ]
        )


def _page_view(
    page: QueuePage, markings: Mapping[str, list[str]], projects: Mapping[ProjectId, Project]
) -> QueuePageView:
    return QueuePageView(
        entries=[_entry_view(QueuePeekEntry, entry, markings, projects) for entry in page.entries],
        next_cursor=page.next_cursor,
    )


@router.get("/queue", response_model=QueuePageView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def get_queue(
    services: Services,
    project: QueryProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> QueuePageView:
    """The hub-ordered ready queue, read-only and keyset-paginated
    — honours reorder/replace + grouping. ``?project=`` narrows it to that project's
    chunks at their whole-queue positions, never reranked; without it, the whole tenant's."""
    statuses = services.chunks.facts.load_live_statuses()
    try:
        page = services.queue.page(QueueList.READY, statuses=statuses, cursor=cursor, limit=limit, project=project)
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor") from exc
    return _page_view(
        page,
        _blocked_markings(services, statuses, [entry.chunk.chunk_id for entry in page.entries]),
        _projects_of(services, page.entries),
    )


@router.put("/queue", response_model=QueuePeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))])
def replace_queue(request: QueueReplaceRequest, services: Services) -> QueuePeekResponse:
    """Idempotent whole-order replacement of the ready queue.

    Resolves every named id against the current ready set: ``409`` names the first id
    that is not ready, ``422`` a duplicate id. An unnamed ready chunk keeps its relative
    order, appended after the named ones."""
    statuses = services.chunks.facts.load_live_statuses()
    _replace(QueueList.READY, request.chunk_ids, services, statuses)
    # A position write changes no chunk's status, so the pre-write `statuses` still
    # holds for the re-render — no second derivation.
    return ReadyQueue.of(services, statuses).view


@router.post(
    "/queue/position", response_model=QueuePeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))]
)
def reposition_queue(request: QueuePositionRequest, services: Services) -> QueuePeekResponse:
    """Single-chunk fractional reorder.

    Resolves both ids against the current ready set: ``409`` names either one if it is
    not ready, ``422`` rejects a self-anchor. ``after_chunk_id=null`` moves the chunk to
    the top of the queue."""
    statuses = services.chunks.facts.load_live_statuses()
    _reposition(QueueList.READY, request.chunk_id, request.after_chunk_id, services, statuses)
    # A position write changes no chunk's status, so the pre-write `statuses` still
    # holds for the re-render — no second derivation.
    return ReadyQueue.of(services, statuses).view


@dto
@dataclass(frozen=True)
class Backlog:
    """The hub-ordered ``not_ready`` list as every peek renders it — position is the
    order itself. ``markings`` and ``projects`` are resolved once in :meth:`of`, alongside
    ``entries``, so :attr:`view` stays a pure projection over already-fetched state rather
    than an I/O-performing property."""

    entries: list[QueueEntry]
    markings: dict[str, list[str]]
    projects: dict[ProjectId, Project]

    @classmethod
    def of(cls, services: TenantServices, statuses: Mapping[str, ChunkStatus]) -> Backlog:
        entries = entries_of(services.queue.ordered_not_ready(statuses=statuses))
        return cls(entries, _blocked_markings(services, statuses), _projects_of(services, entries))

    @property
    def view(self) -> BacklogPeekResponse:
        return BacklogPeekResponse(
            entries=[_entry_view(BacklogPeekEntry, entry, self.markings, self.projects) for entry in self.entries]
        )


def _backlog_page_view(
    page: QueuePage, markings: Mapping[str, list[str]], projects: Mapping[ProjectId, Project]
) -> BacklogPageView:
    return BacklogPageView(
        entries=[_entry_view(BacklogPeekEntry, entry, markings, projects) for entry in page.entries],
        next_cursor=page.next_cursor,
    )


@router.get("/backlog", response_model=BacklogPageView, dependencies=[Depends(require(Permission.QUEUE_REORDER))])
def get_backlog(
    services: Services,
    project: QueryProject,
    cursor: Annotated[str | None, Query()] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_LIMIT)] = DEFAULT_LIMIT,
) -> BacklogPageView:
    """The hub-ordered ``not_ready`` list, read-only and keyset-paginated
    — an operator triage surface, requiring ``queue:reorder`` not ``fleet:view``.
    ``?project=`` narrows it to that project's chunks at their whole-list positions, never
    reranked; without it, the whole tenant's."""
    statuses = services.chunks.facts.load_live_statuses()
    try:
        page = services.queue.page(QueueList.NOT_READY, statuses=statuses, cursor=cursor, limit=limit, project=project)
    except MalformedCursor as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="malformed cursor") from exc
    return _backlog_page_view(
        page,
        _blocked_markings(services, statuses, [entry.chunk.chunk_id for entry in page.entries]),
        _projects_of(services, page.entries),
    )


@router.put("/backlog", response_model=BacklogPeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))])
def replace_backlog(request: BacklogReplaceRequest, services: Services) -> BacklogPeekResponse:
    """Idempotent whole-order replacement of the backlog.

    Resolves every named id against the current ``not_ready`` set: ``409`` names the
    first id that is not ``not_ready``, ``422`` a duplicate id. An unnamed chunk keeps
    its relative order, appended after the named ones."""
    statuses = services.chunks.facts.load_live_statuses()
    _replace(QueueList.NOT_READY, request.chunk_ids, services, statuses)
    # A position write changes no chunk's status, so the pre-write `statuses` still
    # holds for the re-render — no second derivation.
    return Backlog.of(services, statuses).view


@router.post(
    "/backlog/position", response_model=BacklogPeekResponse, dependencies=[Depends(require(Permission.QUEUE_REORDER))]
)
def reposition_backlog(request: BacklogPositionRequest, services: Services) -> BacklogPeekResponse:
    """Single-chunk fractional reorder within the backlog.

    Resolves both ids against the current ``not_ready`` set: ``409`` names either one if
    it is not ``not_ready``, ``422`` rejects a self-anchor. ``after_chunk_id=null`` moves
    the chunk to the top of the backlog."""
    statuses = services.chunks.facts.load_live_statuses()
    _reposition(QueueList.NOT_READY, request.chunk_id, request.after_chunk_id, services, statuses)
    # A position write changes no chunk's status, so the pre-write `statuses` still
    # holds for the re-render — no second derivation.
    return Backlog.of(services, statuses).view


@router.post(
    "/chunks/{chunk_id}/group",
    response_model=ChunkGroupResponse,
    dependencies=[Depends(require(Permission.QUEUE_REORDER))],
)
def group_chunks(
    chunk_id: str,
    request: ChunkGroupRequest,
    services: Services,
) -> object:
    """Merge unacquired chunks into ``chunk_id``. Accepts ``not_ready`` and ``ready``
    participants alike; 409 names the first chunk a runner holds, or one
    already finished, or one of another project than the survivor's. The survivor also
    absorbs each folded chunk's standing dependency edges; 409 refuses a fold that would
    close a cycle."""
    before = chunk_events.ChunkChanged.before(services, chunk_id)
    try:
        result = services.group.group(chunk_id, request.merge_chunk_ids)
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (ChunkNotGroupable, ChunksInDifferentProjects) as exc:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})
    except FoldWouldCloseCycle as exc:
        return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"detail": str(exc)})
    # The survivor's status comes from the group result, never a ``"ready"`` constant:
    # grouping backlog chunks leaves a backlog survivor.
    survivor = result.survivor
    services.events.publish_queue_changed()
    key = f"chunk_grouped:{result.grouped_id}" if result.grouped_id is not None else None
    chunk_events.ChunkChanged.of(services, survivor.chunk_id, prev_status=before.prev_status).publish(
        cause="grouped", status=result.status.value, key=key
    )
    return ChunkGroupResponse(
        chunk_id=survivor.chunk_id,
        work_refs=[WorkRefModel(source=p.source, ref=p.ref) for p in survivor.work_refs],
        merged_chunk_ids=[m for m in request.merge_chunk_ids if m != survivor.chunk_id],
    )
