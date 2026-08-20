"""Work-source item routes (blizzard#358) — the operator-plane editor surface over a
work source's browsable items, human-plane throughout (``reject_runner_principal``).

Every source-addressed route is gated on the source's editor (D4, reads included): an
unknown source is 404, a known one with no editor is 409. The sources listing itself
carries no gate; it renders each source's capability booleans instead."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemRecord, WorkRef
from blizzard.hub.domain.work_items import WorkItemHeldByLiveChunk, WorkItemNotEditable
from blizzard.hub.work_sources.editor import IWorkEditor, WorkItemRefUnknownError
from blizzard.hub.work_sources.source import IWorkSource
from blizzard.wire.work_source import (
    WorkItemAuthorView,
    WorkItemCreateRequest,
    WorkItemPatchRequest,
    WorkItemsListView,
    WorkItemView,
    WorkSourceSummary,
)

router = APIRouter(prefix="/api", tags=["work-sources"], dependencies=[Depends(reject_runner_principal)])


def _require_editor(source: str, services: HubServices) -> tuple[IWorkSource, IWorkEditor]:
    """The named source and its editor, or the D4 refusal: 404 for an unknown source,
    409 for a known one with no editor (a configured forge source, never opted in)."""
    source_obj = services.work_sources.get(source)
    if source_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown work source {source!r}")
    editor = services.work_sources.editor(source)
    if editor is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"work source {source!r} has no editor")
    return source_obj, editor


def _view(item: WorkItemRecord, source_obj: IWorkSource) -> WorkItemView:
    pointer = WorkRef(source=item.source, ref=item.ref)
    return WorkItemView(
        source=item.source,
        ref=item.ref,
        label=source_obj.label(pointer),
        web_url=source_obj.web_url(pointer),
        title=item.title,
        body=item.body,
        author=WorkItemAuthorView(kind=item.author.kind.value, user_id=item.author.user_id),
        stated_priority=item.stated_priority,
        created_at=iso_utc(item.created_at),
        edited_at=iso_utc(item.edited_at),
        closed_at=iso_utc(item.closed_at) if item.closed_at is not None else None,
        closure=item.closure.value if item.closure is not None else None,
    )


@router.get("/work-sources", response_model=list[WorkSourceSummary], dependencies=[Depends(require(FLEET_VIEW))])
def list_work_sources(services: Annotated[HubServices, Depends(get_services)]) -> list[WorkSourceSummary]:
    """Every configured (plus the built-in ``hub``) source's capability booleans — no
    gate of its own, since a client needs this to know which sources gate their items."""
    return [
        WorkSourceSummary(
            name=name,
            annotate=services.work_sources.annotator(name) is not None,
            close=services.work_sources.closer(name) is not None,
            edit=services.work_sources.editor(name) is not None,
        )
        for name in services.work_sources.names()
    ]


@router.get(
    "/work-sources/{source}/items", response_model=WorkItemsListView, dependencies=[Depends(require(FLEET_VIEW))]
)
def list_work_items(source: str, services: Annotated[HubServices, Depends(get_services)]) -> WorkItemsListView:
    """Every item at SOURCE, newest first, open and closed alike. 404/409 per D4."""
    source_obj, editor = _require_editor(source, services)
    return WorkItemsListView(items=[_view(item, source_obj) for item in editor.list()])


@router.post(
    "/work-sources/{source}/items",
    response_model=WorkItemView,
    status_code=status.HTTP_201_CREATED,
)
def create_work_item(
    source: str,
    request: WorkItemCreateRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> WorkItemView:
    """Allocate a fresh item at SOURCE, open, authored by the caller. 404/409 per D4."""
    source_obj, editor = _require_editor(source, services)
    created = editor.create(
        title=request.title,
        body=request.body,
        author=WorkItemAuthor.user(identity.user_id),
        stated_priority=request.stated_priority,
    )
    return _view(created, source_obj)


@router.get(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(FLEET_VIEW))],
)
def get_work_item(source: str, ref: str, services: Annotated[HubServices, Depends(get_services)]) -> WorkItemView:
    """One item at SOURCE by REF, open or closed. 404 for an unknown source, an
    unallocated ref (D9), or a known source with no editor answered as 409 (D4)."""
    source_obj, editor = _require_editor(source, services)
    try:
        item = editor.get(WorkRef(source=source, ref=ref))
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _view(item, source_obj)


@router.patch(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def patch_work_item(
    source: str,
    ref: str,
    request: WorkItemPatchRequest,
    services: Annotated[HubServices, Depends(get_services)],
) -> WorkItemView:
    """Replace the given fields in place, all-or-nothing. 404 for an unknown source or
    an unallocated ref (D9); 409 for a known source with no editor (D4) or an item that
    already carries a closure (D5)."""
    source_obj, editor = _require_editor(source, services)
    pointer = WorkRef(source=source, ref=ref)
    try:
        current = editor.get(pointer)
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    stated_priority = (
        request.stated_priority if "stated_priority" in request.model_fields_set else current.stated_priority
    )
    try:
        updated = editor.edit(
            pointer,
            title=request.title if request.title is not None else current.title,
            body=request.body if request.body is not None else current.body,
            stated_priority=stated_priority,
        )
    except WorkItemNotEditable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _view(updated, source_obj)


@router.delete(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(CHUNK_CONTROL))],
)
def withdraw_work_item(
    source: str,
    ref: str,
    services: Annotated[HubServices, Depends(get_services)],
) -> WorkItemView:
    """Withdraw the item at SOURCE/REF. 404 for an unknown source or an unallocated ref
    (D9); 409 for a known source with no editor (D4), an item that already carries a
    closure, or one a live chunk still holds (D5, D10)."""
    source_obj, editor = _require_editor(source, services)
    pointer = WorkRef(source=source, ref=ref)
    try:
        withdrawn = editor.withdraw(pointer)
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except (WorkItemNotEditable, WorkItemHeldByLiveChunk) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _view(withdrawn, source_obj)
