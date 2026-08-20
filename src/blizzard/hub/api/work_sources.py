"""Work-source item routes (blizzard#358) — the operator-plane editor surface over a
work source's browsable items, human-plane throughout (``reject_runner_principal``).

Every source-addressed route is gated on the source's editor (D4, reads included): an
unknown source is 404, a known one with no editor is 409. The sources listing itself
carries no gate; it renders each source's capability booleans instead."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import CHUNK_CONTROL, FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.edit import UNSET
from blizzard.hub.domain.graph_authoring import DefaultGraphRetired
from blizzard.hub.domain.work import WorkItemAuthor, WorkItemPriority, WorkItemRecord, WorkRef
from blizzard.hub.domain.work_items import WorkItemEdit, WorkItemHeldByLiveChunk, WorkItemNotEditable
from blizzard.hub.work_sources.editor import IWorkEditor, WorkItemRefUnknownError
from blizzard.hub.work_sources.source import IWorkSource
from blizzard.wire.work_source import (
    WorkItemAuthorView,
    WorkItemCreateRequest,
    WorkItemCreateResponse,
    WorkItemPatchRequest,
    WorkItemsListView,
    WorkItemView,
    WorkSourcesListView,
    WorkSourceSummary,
)

router = APIRouter(prefix="/api", tags=["work-sources"], dependencies=[Depends(reject_runner_principal)])


def _require_editor(source: str, services: HubServices) -> tuple[IWorkSource, IWorkEditor]:
    """The named source and its editor, or the D4 refusal: 404 for an unknown source,
    409 for a known one with no editor — a structural refusal for every source but
    ``hub``, since no ``[[work_source]]`` field could ever opt a configured source into
    editing (``blizzard-context:/architecture/system-shape.md``), not merely "not opted
    in" the way ``annotate``/``close`` are."""
    source_obj = services.work_sources.get(source)
    if source_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown work source {source!r}")
    editor = services.work_sources.editor(source)
    if editor is None:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"work source {source!r} has no editor")
    return source_obj, editor


def _stripped(value: str, field_name: str) -> str:
    text = value.strip()
    if not text:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_ENTITY, detail=f"{field_name} must not be blank")
    return text


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
        stated_priority=WorkItemPriority(item.stated_priority) if item.stated_priority is not None else None,
        created_at=iso_utc(item.created_at),
        edited_at=iso_utc(item.edited_at),
        closed_at=iso_utc(item.closed_at) if item.closed_at is not None else None,
        closure=item.closure,
    )


@router.get("/work-sources", response_model=WorkSourcesListView, dependencies=[Depends(require(FLEET_VIEW))])
def list_work_sources(services: Annotated[HubServices, Depends(get_services)]) -> WorkSourcesListView:
    """Every configured (plus the built-in ``hub``) source's capability booleans — no
    gate of its own, since a client needs this to know which sources gate their items."""
    return WorkSourcesListView(
        sources=[
            WorkSourceSummary(
                name=name,
                annotate=services.work_sources.annotator(name) is not None,
                close=services.work_sources.closer(name) is not None,
                edit=services.work_sources.editor(name) is not None,
            )
            for name in services.work_sources.names()
        ]
    )


@router.get(
    "/work-sources/{source}/items", response_model=WorkItemsListView, dependencies=[Depends(require(FLEET_VIEW))]
)
def list_work_items(
    source: str,
    services: Annotated[HubServices, Depends(get_services)],
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> WorkItemsListView:
    """Up to LIMIT items at SOURCE, newest first, open and closed alike. 404/409 per D4."""
    source_obj, editor = _require_editor(source, services)
    return WorkItemsListView(items=[_view(item, source_obj) for item in editor.list(limit=limit)])


@router.post(
    "/work-sources/{source}/items",
    response_model=WorkItemCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_work_item(
    source: str,
    request: WorkItemCreateRequest,
    services: Annotated[HubServices, Depends(get_services)],
    identity: Annotated[ResolvedIdentity, Depends(require(CHUNK_CONTROL))],
) -> WorkItemCreateResponse:
    """Allocate a fresh item at SOURCE, open, authored by the caller, and mint its
    resting ``not_ready`` chunk in the same transaction (blizzard#359). 404/409 per D4,
    422 for a blank title or body, 503 if every graph named after the packaged default
    has been retired (the operator's brake, mirroring ``POST /chunks``)."""
    source_obj, editor = _require_editor(source, services)
    try:
        graph = services.graph_mint.ensure_default(
            services.default_graph_doc, definition_yaml=services.default_graph_yaml
        )
    except DefaultGraphRetired as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    created = editor.create(
        title=_stripped(request.title, "title"),
        body=_stripped(request.body, "body"),
        author=WorkItemAuthor.user(identity.user_id),
        stated_priority=request.stated_priority,
        graph=graph,
    )
    # A freshly minted chunk rests `not_ready`, exactly as a `POST /chunks` ingest does.
    chunk_events.ChunkChanged.of(services, created.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{created.chunk_id}"
    )
    return WorkItemCreateResponse(**_view(created.item, source_obj).model_dump(), chunk_id=created.chunk_id)


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
    already carries a closure (D5); 422 for a blank title or body."""
    source_obj, editor = _require_editor(source, services)
    pointer = WorkRef(source=source, ref=ref)
    # Sentinel-tagged rather than merged here: filling an omitted field at the edge needs a
    # second, unguarded read of the pointer, which races a concurrent withdrawal.
    edit = WorkItemEdit(
        title=_stripped(request.title, "title") if request.title is not None else UNSET,
        body=_stripped(request.body, "body") if request.body is not None else UNSET,
        stated_priority=request.stated_priority if "stated_priority" in request.model_fields_set else UNSET,
    )
    try:
        updated = editor.edit(pointer, edit)
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
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
