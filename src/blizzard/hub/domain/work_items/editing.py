"""Hub-owned work item editing plus delivery closure — create, in-place edit, withdraw, deliver.

Holds the *write* work-item repository, reached only through a work-source binding's
``IWorkEditor``/``IWorkCloser``. Orchestration only: legality, the text invariant, and an edit's
resolution are :mod:`blizzard.hub.domain.work_items.model`'s; this service reads the clock, asks the
model, writes, and turns a lost write race into the model's refusal."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import dto
from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority
from blizzard.hub.config import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import require_no_live_holder
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkFacts,
    HubWorkItem,
    IWriteWorkItemRepository,
    WorkItemAuthor,
    WorkRef,
    mint_chunk,
)
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.operations.delete import ChunkHasDependents, ChunkNotDeletable, DeleteService
from blizzard.hub.domain.work_items.model import (
    Transition,
    WorkItemEdit,
    WorkItemNotEditable,
    WorkItemState,
    WorkItemText,
    WorkItemVerb,
    require_open_for,
    require_withdrawn,
)


def prepare_mint(
    items: IWriteWorkItemRepository,
    work_refs: IReadChunkWorkRefsRepository,
    clock: IClock,
    source: str,
    *,
    graph: Graph,
    default_model: list[str] | None = None,
    default_effort: str | None = None,
    default_harnesses: list[str] | None = None,
) -> tuple[WorkRef, Chunk, datetime]:
    """The guard sequence every item-minting create path shares: allocate the ref,
    refuse a live holder, mint the resting chunk. ``default_model``/``default_effort``/
    ``default_harnesses`` fall back to ``mint_chunk``'s own empty-preference default."""
    ref = items.allocate_ref(source)
    pointer = WorkRef(source=source, ref=ref)
    require_no_live_holder(work_refs, pointer)
    at = clock.now()
    chunk = mint_chunk(
        [pointer],
        graph_id=graph.graph_id,
        at=at,
        default_model=default_model,
        default_effort=default_effort,
        default_harnesses=default_harnesses,
    )
    return pointer, chunk, at


@dto
@dataclass(frozen=True)
class CreatedWorkItem:
    """The result of filing a hub-owned work item — the item plus the
    id of the ``not_ready`` chunk its creation mints in the same transaction."""

    item: HubWorkItem
    chunk_id: str


@dto
@dataclass(frozen=True)
class WithdrawnWorkItem:
    """The result of withdrawing a hub-owned work item — the item itself,
    plus the cascade-deleted holder chunk's id, its pre-delete status, and the fresh
    ``chunk_deleted.id`` when withdrawal cascaded into deleting an unacquired holder;
    all three ``None`` when it did not."""

    item: HubWorkItem
    deleted_chunk_id: str | None = None
    deleted_chunk_status: str | None = None
    deleted_chunk_fact_id: int | None = None


class WorkItemHeldByLiveChunk(Exception):
    """A withdrawal targeted a pointer a live (non-terminal) chunk still holds — mirrors
    ``IngestConflict`` (``hub/domain/chunk/ingest.py``): withdrawing under a running chunk would
    degrade that chunk's work-item read to an unresolvable error."""

    def __init__(self, pointer: WorkRef, chunk_id: str) -> None:
        super().__init__(f"{pointer.source}:{pointer.ref} is held by live chunk {chunk_id}")
        self.pointer = pointer
        self.chunk_id = chunk_id


class WorkItemHeldByDependents(Exception):
    """Withdrawal cascaded into deleting an unacquired holder, but that holder is a
    standing prerequisite for other chunks — names the edge, distinct from
    :class:`WorkItemHeldByLiveChunk`, which names a live holder instead."""

    def __init__(self, pointer: WorkRef, chunk_id: str, dependent_chunk_ids: list[str]) -> None:
        super().__init__(
            f"{pointer.source}:{pointer.ref}'s holder {chunk_id} is a standing prerequisite "
            f"for {', '.join(dependent_chunk_ids)}"
        )
        self.pointer = pointer
        self.chunk_id = chunk_id
        self.dependent_chunk_ids = dependent_chunk_ids


class WorkItemEditService:
    """Create, edit, withdraw, or deliver a hub-owned work item — the write half a
    work-source binding's editor/closer delegates to once it has resolved a pointer
    to a loaded record."""

    def __init__(
        self,
        *,
        items: IWriteWorkItemRepository,
        work_refs: IReadChunkWorkRefsRepository,
        record: IReadChunkRecordRepository,
        facts: IReadChunkFactsRepository,
        clock: IClock,
        delete: DeleteService,
    ) -> None:
        self._items = items
        self._work_refs = work_refs
        self._record = record
        self._facts = facts
        self._clock = clock
        self._delete = delete

    def create(
        self,
        *,
        source: str,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: WorkItemPriority | None,
        graph: Graph,
    ) -> CreatedWorkItem:
        """File the item and mint its resting chunk in one transaction, pinned to ``graph``, holding the
        pointer this call allocates. An out-of-band ingest of the same ref can pre-empt it, raising
        :class:`~blizzard.hub.domain.chunk.ingest.IngestConflict` and burning the ref. A blank title or
        body raises :class:`~blizzard.hub.domain.work_items.model.WorkItemFieldBlank` before allocating."""
        text = WorkItemText.of(title=title, body=body)
        pointer, chunk, at = prepare_mint(self._items, self._work_refs, self._clock, source, graph=graph)
        item = self._items.create_with_chunk(
            pointer=pointer,
            title=text.title,
            body=text.body,
            author=author,
            stated_priority=stated_priority.value if stated_priority is not None else None,
            at=at,
            chunk=chunk,
        )
        return CreatedWorkItem(item=item, chunk_id=chunk.chunk_id)

    def materialize_create(
        self,
        proposal_id: str,
        *,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: str | None,
        graph: Graph,
    ) -> bool:
        """The materialization sweep's own ``create`` path: :meth:`create`'s
        guard sequence, always into the reserved hub source, landing through
        :meth:`~blizzard.hub.domain.chunk.model.IWriteWorkItemRepository.materialize_create` so
        the mint and the outcome fact are one transaction. Raises
        :class:`~blizzard.hub.domain.chunk.ingest.IngestConflict` exactly as :meth:`create`
        does, and :class:`~blizzard.hub.domain.work_items.model.WorkItemFieldBlank` for a blank
        title or body; returns ``False`` when ``proposal_id`` was already judged."""
        text = WorkItemText.of(title=title, body=body)
        pointer, chunk, at = prepare_mint(
            self._items, self._work_refs, self._clock, RESERVED_HUB_SOURCE_NAME, graph=graph
        )
        return self._items.materialize_create(
            proposal_id=proposal_id,
            pointer=pointer,
            title=text.title,
            body=text.body,
            author=author,
            stated_priority=stated_priority,
            at=at,
            chunk=chunk,
        )

    def accept_create(
        self,
        proposal_id: str,
        *,
        title: str,
        body: str,
        author: WorkItemAuthor,
        graph: Graph,
        reason: str | None,
        closed_by: str,
    ) -> CreatedWorkItem | None:
        """A garden-proposal acceptance's mint path: :meth:`create`'s guard sequence into the reserved hub
        source, writing the item, its chunk, and the closure row on one connection. Raises as
        :meth:`create` does; returns ``None`` when already closed."""
        text = WorkItemText.of(title=title, body=body)
        pointer, chunk, at = prepare_mint(
            self._items, self._work_refs, self._clock, RESERVED_HUB_SOURCE_NAME, graph=graph
        )
        item = self._items.accept_create(
            proposal_id=proposal_id,
            pointer=pointer,
            title=text.title,
            body=text.body,
            author=author,
            at=at,
            chunk=chunk,
            reason=reason,
            closed_by=closed_by,
        )
        if item is None:
            return None
        return CreatedWorkItem(item=item, chunk_id=chunk.chunk_id)

    def edit(self, item: HubWorkItem, edit: WorkItemEdit) -> HubWorkItem:
        """Write ``edit``'s revision of ``item`` — the record this call itself guards — in
        place; raises :class:`~blizzard.hub.domain.work_items.model.WorkItemNotEditable` when
        ``item`` already carries a closure, checked here and re-checked by the store's own
        ``closed_at IS NULL`` guard against a closure racing in between."""
        require_open_for(item, WorkItemVerb.EDIT)
        revision = edit.resolve_against(item)
        updated = self._items.edit(
            item.source,
            item.ref,
            title=revision.title,
            body=revision.body,
            stated_priority=revision.stated_priority,
            at=self._clock.now(),
        )
        if updated is None:
            current = self._items.get(item.source, item.ref)
            assert current is not None and current.closure is not None
            raise WorkItemNotEditable(current.work_item_id, current.closure)
        return updated

    def withdraw(self, item: HubWorkItem, *, by: str) -> WithdrawnWorkItem:
        """Close ``item`` as withdrawn; raises :class:`WorkItemNotEditable` when already
        closed. An unacquired holder is deleted via
        :class:`~blizzard.hub.domain.operations.delete.DeleteService` instead of refusing;
        :class:`WorkItemHeldByLiveChunk` still raises for a runner- or human-held
        one. Names the cascade-deleted chunk, if any, for the caller's own delete frame."""
        require_open_for(item, WorkItemVerb.WITHDRAW)
        holder = self._work_refs.find_live_holder(item.pointer)
        if holder is None:
            closed = self._items.close(item.source, item.ref, closure=WorkItemClosure.WITHDRAWN, at=self._clock.now())
            require_withdrawn(closed)
            return WithdrawnWorkItem(item=closed)
        chunk = self._record.get(holder)
        if chunk is None:
            raise ChunkNotFound(holder)
        facts = self._facts.load_facts(holder)
        prev_status = ChunkFacts.or_default(facts).status().value
        try:
            deleted_id = self._delete.delete(chunk, by=by)
        except ChunkNotDeletable as exc:
            raise WorkItemHeldByLiveChunk(item.pointer, holder) from exc
        except ChunkHasDependents as exc:
            raise WorkItemHeldByDependents(item.pointer, holder, exc.dependent_chunk_ids) from exc
        updated = self._items.get(item.source, item.ref)
        assert updated is not None
        return WithdrawnWorkItem(
            item=updated, deleted_chunk_id=holder, deleted_chunk_status=prev_status, deleted_chunk_fact_id=deleted_id
        )

    def deliver(self, item: HubWorkItem) -> HubWorkItem:
        """Close ``item`` as delivered — the close-intent drainer's own write path. A
        live chunk holding the pointer is the expected caller, not a conflict to block.
        An item already closed — delivered by an earlier attempt, or withdrawn after its
        holder landed — is a no-op that writes nothing and keeps its closure."""
        if WorkItemState.of(item).on(WorkItemVerb.DELIVER) is Transition.NOOP:
            return item
        return self._items.close(item.source, item.ref, closure=WorkItemClosure.DELIVERED, at=self._clock.now())
