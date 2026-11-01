"""The work-item pass-through read shared by the per-chunk and the bulk routes.

One read path for both, so the two cannot drift: a bulk read is the per-chunk read over many chunks, with the calls
bounded by the sources the chunks' pointers name rather than by the pointers (``bzh:bulk-reconstitution``). The
orchestration returns domain values (:mod:`blizzard.hub.work_sources.reads`); this maps them to the wire."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.composition import TenantServices
from blizzard.hub.work_sources.reads import ReadWorkItem, read_work_items
from blizzard.hub.work_sources.source import AuthorView
from blizzard.wire.chunk import ChunkWorkItemsView, WorkItemEntry
from blizzard.wire.work_source import WorkItemAuthorView

#: The most chunk ids one bulk read names.
MAX_BULK_CHUNK_IDS = 100

router = APIRouter(
    prefix="/api",
    tags=["chunks"],
    dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)],
)


def read_chunk_work_items(
    chunk_ids: Sequence[str], services: TenantServices, *, include_comments: bool
) -> dict[str, list[WorkItemEntry]]:
    """Each resolved chunk's work-item entries, keyed by chunk id; an id that does not resolve is absent. One chunk
    read, one live-holder read, one read per source and one runner-name read serve the whole set."""
    chunks = services.chunks.record.get_many(list(dict.fromkeys(chunk_ids)))
    if not chunks:
        return {}
    refs = {chunk_id: chunk.work_refs for chunk_id, chunk in chunks.items()}
    holders = services.chunks.work_refs.live_holders([ref for chunk_refs in refs.values() for ref in chunk_refs])
    read = read_work_items(refs, sources=services.work_sources, live_holders=holders, include_comments=include_comments)
    fetched_at = iso_utc(services.clock.now())
    runner_names = services.registry.names_for(
        item.author.runner_id
        for items in read.values()
        for item in items
        if item.author is not None and item.author.runner_id is not None
    )
    return {chunk_id: [_entry(item, fetched_at, runner_names) for item in items] for chunk_id, items in read.items()}


def _entry(item: ReadWorkItem, fetched_at: str, runner_names: Mapping[str, str]) -> WorkItemEntry:
    return WorkItemEntry(
        source=item.pointer.source,
        ref=item.pointer.ref,
        slug=item.slug,
        label=item.label,
        web_url=item.web_url,
        hub_source=item.hub_source,
        fetched_at=fetched_at,
        title=item.title,
        body=item.body,
        comments=list(item.comments),
        error=item.error,
        author=_author_view(item.author, runner_names) if item.author is not None else None,
        stated_priority=item.stated_priority,
    )


def _author_view(author: AuthorView, runner_names: Mapping[str, str]) -> WorkItemAuthorView:
    """A seam-level :class:`AuthorView` onto the wire — no vocabulary resolved here: the
    source already resolved it, this only reshapes the fields and names a fleet author's
    runner out of ``runner_names``, the caller's one batched read."""
    return WorkItemAuthorView(
        kind=author.kind,
        user_id=author.user_id,
        login=author.login,
        runner_id=author.runner_id,
        runner_name=runner_names.get(author.runner_id) if author.runner_id is not None else None,
        chunk_id=author.chunk_id,
        node_name=author.node_name,
    )


@router.get(
    "/work-items",
    response_model=ChunkWorkItemsView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def get_bulk_work_items(
    services: Services,
    chunk_id: Annotated[list[str], Query(max_length=MAX_BULK_CHUNK_IDS)],
    include_comments: bool = False,
) -> ChunkWorkItemsView:
    """Many chunks' pass-through work items in one read — repeatable ``chunk_id``, at most 100 (``422`` beyond).
    Each GitHub source is asked once however many items it holds. An unknown or ephemeral chunk id is omitted, never
    a 404; a per-pointer failure is that entry's own ``error``. Comments are empty unless ``include_comments``."""
    if not chunk_id:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail="name at least one chunk_id")
    return ChunkWorkItemsView(chunks=read_chunk_work_items(chunk_id, services, include_comments=include_comments))
