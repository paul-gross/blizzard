"""A routine run's mint — the run's item and resting chunk, then its run identity, written in the caller's unit.
A hand-started run and a cadence errand's run both mint through it."""

from __future__ import annotations

from datetime import datetime

from blizzard.hub.domain.chunk.model import Chunk, ErrandMark, HubWorkItem, WorkItemAuthor, WorkRef
from blizzard.hub.domain.chunk.ports.record import IWriteChunkRecordRepository
from blizzard.hub.domain.garden.run_context import IWriteRunContextRepository, RunContext
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.domain.work_items.editing import mint_item
from blizzard.hub.domain.work_items.ports import IWriteWorkItemRepository


def mint_run(
    items: IWriteWorkItemRepository,
    chunks: IWriteChunkRecordRepository,
    run_contexts: IWriteRunContextRepository,
    *,
    pointer: WorkRef,
    title: str,
    body: str,
    author: WorkItemAuthor,
    project_id: str,
    routine_name: str,
    scope_slug: str,
    run_mode: str,
    at: datetime,
    chunk: Chunk,
    transaction: LockedTransaction,
    errand: ErrandMark | None = None,
) -> HubWorkItem:
    """Write the run's item and ``chunk`` (:func:`~blizzard.hub.domain.work_items.editing.mint_item`), then its run
    identity in the routine's ``project_id``, marked with ``errand`` when a cadence mints it, all in
    ``transaction``, which locks the chunk's work-ref keys. No promote rows are written: the chunk rests
    ``not_ready``."""
    item = mint_item(
        items,
        chunks,
        pointer=pointer,
        title=title,
        body=body,
        author=author,
        stated_priority=None,
        at=at,
        chunk=chunk,
        transaction=transaction,
        routine_name=routine_name,
        scope_slug=scope_slug,
        run_mode=run_mode,
    )
    run_contexts.record(
        item.work_item_id,
        RunContext(
            routine_name=routine_name,
            scope_slug=scope_slug,
            mode=run_mode,
            project_id=project_id,
            errand=errand,
        ),
        transaction=transaction,
    )
    return item
