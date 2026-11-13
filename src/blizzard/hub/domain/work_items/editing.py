"""Hub-owned work item editing plus delivery closure — create, in-place edit, withdraw, deliver.

Holds the *write* work-item repository, reached only through a work-source binding's
``IWorkEditor``/``IWorkCloser``. Orchestration only: legality, the text invariant, and an edit's
resolution are :mod:`blizzard.hub.domain.work_items.model`'s; this service declares the item's key, re-reads
the item under it, asks the model, and writes."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.foundation.work_items import WorkItemClosure, WorkItemPriority
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import require_no_live_holder
from blizzard.hub.domain.chunk.model import (
    Chunk,
    ChunkDraft,
    ChunkFacts,
    HubWorkItem,
    WorkItemAuthor,
    WorkRef,
    draft_chunk,
)
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository, IWriteChunkRecordRepository
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository, work_ref_lock
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.kernel.locks import LockKey
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction
from blizzard.hub.domain.operations.delete import (
    ChunkHasDependents,
    ChunkNotDeletable,
    DeleteService,
    work_item_lock,
)
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.domain.urgency.model import Urgency
from blizzard.hub.domain.urgency.services import MintAhead, TenantDefault
from blizzard.hub.domain.work_items.model import (
    Transition,
    WorkItemEdit,
    WorkItemNotEditable,
    WorkItemState,
    WorkItemText,
    WorkItemVerb,
    require_open_for,
)
from blizzard.hub.domain.work_items.ports import IWriteWorkItemRepository


def prepare_mint(
    items: IWriteWorkItemRepository,
    work_refs: IReadChunkWorkRefsRepository,
    clock: IClock,
    source: str,
    *,
    graph: Graph,
    project_id: ProjectId,
    default_model: list[str] | None = None,
    default_effort: str | None = None,
    default_harnesses: list[str] | None = None,
) -> tuple[WorkRef, ChunkDraft, datetime]:
    """The guard sequence every item-minting create path shares, run before its unit: allocate the ref,
    refuse a live holder, draft the resting chunk into ``project_id`` — the project each path
    names, never one inferred. The draft is minted inside the path's unit, with the default urgency read
    under its locks. ``default_model``/``default_effort``/``default_harnesses`` fall
    back to ``draft_chunk``'s own empty-preference default."""
    ref = items.allocate_ref(source)
    pointer = WorkRef(source=source, ref=ref)
    require_no_live_holder(work_refs, pointer)
    at = clock.now()
    draft = draft_chunk(
        [pointer],
        graph_id=graph.graph_id,
        at=at,
        project_id=project_id,
        default_model=default_model,
        default_effort=default_effort,
        default_harnesses=default_harnesses,
    )
    return pointer, draft, at


def mint_item(
    items: IWriteWorkItemRepository,
    chunks: IWriteChunkRecordRepository,
    *,
    pointer: WorkRef,
    title: str,
    body: str,
    author: WorkItemAuthor,
    stated_priority: str | None,
    at: datetime,
    chunk: Chunk,
    transaction: LockedTransaction,
    routine_name: str | None = None,
    scope_slug: str | None = None,
    run_mode: str | None = None,
) -> HubWorkItem:
    """The mint pair every item-minting path writes inside its caller's unit: the item at ``pointer``, then its
    resting ``chunk``. ``transaction`` must lock the chunk's :func:`work_ref_lock` keys;
    ``routine_name``/``scope_slug``/``run_mode`` mark a routine run's item."""
    item = items.create(
        pointer=pointer,
        title=title,
        body=body,
        author=author,
        stated_priority=stated_priority,
        at=at,
        project_id=chunk.project_id,
        routine_name=routine_name,
        scope_slug=scope_slug,
        run_mode=run_mode,
        transaction=transaction,
    )
    chunks.mint(chunk, transaction=transaction)
    return item


@domain_model
@dataclass(frozen=True)
class DraftedMint:
    """A hub item's mint prepared ahead of its unit: the validated text, the allocated pointer, the chunk's draft,
    and the urgency reads its unit locks."""

    text: WorkItemText
    pointer: WorkRef
    draft: ChunkDraft
    at: datetime
    ahead: MintAhead

    def lock_keys(self) -> list[LockKey]:
        """The keys the minting unit declares: the urgency keys and the pointer's :func:`work_ref_lock`."""
        return [*self.ahead.lock_keys(), work_ref_lock(self.pointer)]


@domain_model
@dataclass(frozen=True)
class CreatedWorkItem:
    """The result of filing a hub-owned work item — the item plus the
    id of the ``not_ready`` chunk its creation mints in the same transaction."""

    item: HubWorkItem
    chunk_id: str


@domain_model
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


class WithdrawalLostToClosure(WorkItemNotEditable):
    """A withdrawal whose holder-chunk deletion committed, but a delivery closed the item
    first — the refusal still carries the deletion's facts so the caller can publish it."""

    def __init__(self, closed: HubWorkItem, deletion: WithdrawnWorkItem) -> None:
        assert closed.closure is not None
        super().__init__(closed.work_item_id, closed.closure)
        self.deletion = deletion


class WorkItemHeldByLiveChunk(Exception):
    """A withdrawal targeted a pointer a live (non-terminal) chunk still holds — mirrors
    ``IngestConflict`` (``src/blizzard/hub/domain/chunk/ingest.py``): withdrawing under a running chunk would
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
        chunks: IWriteChunkRecordRepository,
        facts: IReadChunkFactsRepository,
        clock: IClock,
        delete: DeleteService,
        default: TenantDefault,
        transactions: IHubTransactions,
    ) -> None:
        self._items = items
        self._default = default
        self._transactions = transactions
        self._work_refs = work_refs
        self._record = record
        self._chunks = chunks
        self._facts = facts
        self._clock = clock
        self._delete = delete

    @property
    def delete(self) -> DeleteService:
        return self._delete

    def create(
        self,
        *,
        source: str,
        title: str,
        body: str,
        author: WorkItemAuthor,
        stated_priority: WorkItemPriority | None,
        graph: Graph,
        project_id: ProjectId,
        urgency: Urgency | None = None,
    ) -> CreatedWorkItem:
        """File the item and mint its resting chunk of ``project_id`` on ``graph`` in one unit, carrying the stated
        ``urgency`` or the tenant's default, re-read under their locks. A raced out-of-band ingest raises
        :class:`~blizzard.hub.domain.chunk.ingest.IngestConflict`, burning the ref; a blank title or body raises
        :class:`~blizzard.hub.domain.work_items.model.WorkItemFieldBlank` before allocating."""
        drafted = self.draft_mint(source, title=title, body=body, graph=graph, project_id=project_id, urgency=urgency)
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=drafted.lock_keys())
            return self.mint_drafted(
                drafted,
                author=author,
                stated_priority=stated_priority.value if stated_priority is not None else None,
                transaction=locked,
            )

    def draft_mint(
        self,
        source: str,
        *,
        title: str,
        body: str,
        graph: Graph,
        project_id: ProjectId,
        urgency: Urgency | None = None,
    ) -> DraftedMint:
        """:func:`prepare_mint`'s guard sequence for an item filed into ``source`` and ``project_id`` on ``graph``,
        ahead of the unit that mints it: a blank title or body raises
        :class:`~blizzard.hub.domain.work_items.model.WorkItemFieldBlank` before allocating, a live holder raises as
        :func:`prepare_mint` does."""
        text = WorkItemText.of(title=title, body=body)
        pointer, draft, at = prepare_mint(
            self._items, self._work_refs, self._clock, source, graph=graph, project_id=project_id
        )
        return DraftedMint(text=text, pointer=pointer, draft=draft, at=at, ahead=self._default.ahead(stated=urgency))

    def mint_drafted(
        self,
        drafted: DraftedMint,
        *,
        author: WorkItemAuthor,
        stated_priority: str | None,
        transaction: LockedTransaction,
    ) -> CreatedWorkItem:
        """Mint ``drafted``'s item and chunk (:func:`mint_item`) in the caller's ``transaction``, which declares
        :meth:`DraftedMint.lock_keys`; the chunk carries the urgency resolved under them."""
        chunk = drafted.draft.mint(self._default.resolve(drafted.ahead, transaction).urgency_id)
        item = mint_item(
            self._items,
            self._chunks,
            pointer=drafted.pointer,
            title=drafted.text.title,
            body=drafted.text.body,
            author=author,
            stated_priority=stated_priority,
            at=drafted.at,
            chunk=chunk,
            transaction=transaction,
        )
        return CreatedWorkItem(item=item, chunk_id=chunk.chunk_id)

    def edit(self, item: HubWorkItem, edit: WorkItemEdit) -> HubWorkItem:
        """Write ``edit``'s revision of ``item`` in place, in a unit declaring its
        :func:`~blizzard.hub.domain.operations.delete.work_item_lock`; raises
        :class:`~blizzard.hub.domain.work_items.model.WorkItemNotEditable` when ``item``, re-read under
        that key, carries a closure."""
        require_open_for(item, WorkItemVerb.EDIT)
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[work_item_lock(item.pointer)])
            current = self._items.get(item.source, item.ref, transaction=locked)
            assert current is not None, "a hub work item is never deleted"
            require_open_for(current, WorkItemVerb.EDIT)
            revision = edit.resolve_against(current)
            return self._items.edit(
                item.source,
                item.ref,
                title=revision.title,
                body=revision.body,
                stated_priority=revision.stated_priority,
                at=self._clock.now(),
                transaction=locked,
            )

    def withdraw(self, item: HubWorkItem, *, by: str) -> WithdrawnWorkItem:
        """Close ``item`` as withdrawn; raises :class:`WorkItemNotEditable` when already
        closed. An unacquired holder is deleted via
        :class:`~blizzard.hub.domain.operations.delete.DeleteService` instead of refusing;
        :class:`WorkItemHeldByLiveChunk` still raises for a runner- or human-held
        one. Names the cascade-deleted chunk, if any, for the caller's own delete frame. A
        delivery that closed the item first refuses with :class:`WithdrawalLostToClosure`
        after the holder's deletion has committed, carrying the same deletion facts."""
        require_open_for(item, WorkItemVerb.WITHDRAW)
        holder = self._work_refs.find_live_holder(item.pointer)
        if holder is None:
            return WithdrawnWorkItem(item=self._withdraw_unheld(item))
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
        withdrawn = WithdrawnWorkItem(
            item=updated, deleted_chunk_id=holder, deleted_chunk_status=prev_status, deleted_chunk_fact_id=deleted_id
        )
        if updated.closure is not WorkItemClosure.WITHDRAWN:
            raise WithdrawalLostToClosure(updated, withdrawn)
        return withdrawn

    def _withdraw_unheld(self, item: HubWorkItem) -> HubWorkItem:
        """Close ``item``, which no live chunk holds, as withdrawn in a unit declaring its key, refusing
        :class:`WorkItemNotEditable` with the closure the re-read under that key finds standing."""
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[work_item_lock(item.pointer)])
            current = self._items.get(item.source, item.ref, transaction=locked)
            assert current is not None, "a hub work item is never deleted"
            require_open_for(current, WorkItemVerb.WITHDRAW)
            return self._items.close(
                item.source, item.ref, closure=WorkItemClosure.WITHDRAWN, at=self._clock.now(), transaction=locked
            )

    def deliver(self, item: HubWorkItem) -> HubWorkItem:
        """Close ``item`` as delivered — the close-intent drainer's own write path. A
        live chunk holding the pointer is the expected caller, not a conflict to block.
        An item already closed when re-read under its key — delivered by an earlier attempt,
        or withdrawn after its holder landed — is a no-op that writes nothing and returns
        the item carrying the closure that stands."""
        if WorkItemState.of(item).on(WorkItemVerb.DELIVER) is Transition.NOOP:
            return item
        with self._transactions.begin() as transaction:
            locked = transaction.lock_set(keys=[work_item_lock(item.pointer)])
            current = self._items.get(item.source, item.ref, transaction=locked)
            assert current is not None, "a hub work item is never deleted"
            if WorkItemState.of(current).on(WorkItemVerb.DELIVER) is Transition.NOOP:
                return current
            return self._items.close(
                item.source, item.ref, closure=WorkItemClosure.DELIVERED, at=self._clock.now(), transaction=locked
            )
