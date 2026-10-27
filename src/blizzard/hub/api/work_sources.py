"""Work-source routes — the configured-record verbs (create, list, show, patch, retire,
enable) and the operator-plane editor surface over a work source's browsable items,
human-plane throughout (``reject_runner_principal``). The record verbs write only through
``ConfigAuthoring``; the actor and door come from the request's :class:`ChangeContext`.

Every source-addressed route is gated on the source's editor (reads included): an
unknown source is 404, a known one with no editor is 409. The sources listing itself
carries no gate; it renders each source's capability booleans instead."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Annotated

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Response, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.work_items import WorkItemPriority
from blizzard.hub.api import chunk_events
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_views import ingest_conflict_response
from blizzard.hub.api.deps import Services
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.forge_warnings import warned
from blizzard.hub.api.project_params import QueryProject, project_of_call
from blizzard.hub.api.user_names import user_names
from blizzard.hub.api.work_source_params import PathSourceKey, PathWorkSource, resolved_work_source
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor, WorkItemAuthorKind, WorkRef
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forge_resolution import FORGE_FIELD, ForgeChoice
from blizzard.hub.domain.config.forges import ConfiguredForge, ForgeOriginTaken, ForgeSlugTaken
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    WorkSourceDraft,
    WorkSourceEdit,
    WorkSourceLocatorTaken,
    WorkSourceSlugTaken,
    is_built_in,
)
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.work_items.editing import (
    WithdrawalLostToClosure,
    WithdrawnWorkItem,
    WorkItemHeldByDependents,
    WorkItemHeldByLiveChunk,
)
from blizzard.hub.domain.work_items.model import WorkItemEdit, WorkItemFieldBlank, WorkItemNotEditable, WorkItemText
from blizzard.hub.work_sources.editor import IWorkEditor, WorkItemRefUnknownError
from blizzard.hub.work_sources.source import AuthorView, IWorkSource, resolve_author_view
from blizzard.wire.forge import OldFieldWarning
from blizzard.wire.work_source import (
    WorkItemAuthorView,
    WorkItemCreateRequest,
    WorkItemCreateResponse,
    WorkItemPatchRequest,
    WorkItemsListView,
    WorkItemView,
    WorkSourceCreateRequest,
    WorkSourcePatchRequest,
    WorkSourcesListView,
    WorkSourceSummary,
)

router = APIRouter(
    prefix="/api", tags=["work-sources"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)


def _require_editor(source: str, services: TenantServices) -> tuple[IWorkSource, IWorkEditor]:
    """The source keyed ``source`` and its editor, or the refusal: 404 for an unknown source,
    409 for a known one with no editor — a structural refusal for every source but
    ``hub``, since no work-source record field could ever opt a configured source into
    editing (``blizzard-context:/architecture/system-shape.md``), not merely "not opted
    in" the way ``annotate`` is."""
    source_obj = services.work_sources.get(source)
    if source_obj is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown work source {source!r}")
    editor = services.work_sources.editor(source)
    if editor is None:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"work source {services.work_sources.source_label(source)!r} has no editor",
        )
    return source_obj, editor


def _blank(exc: WorkItemFieldBlank) -> HTTPException:
    return HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc))


def _runner_names(services: TenantServices, authors: Iterable[WorkItemAuthor]) -> dict[str, str]:
    """The registered names of the fleet authors among ``authors`` — one batched registry read,
    none at all when every author is a person."""
    return services.registry.names_for(author.runner_id for author in authors if author.runner_id is not None)


def _view(
    item: HubWorkItem,
    source_obj: IWorkSource,
    author: AuthorView,
    *,
    slug: str,
    live_holder: str | None,
    runner_names: Mapping[str, str],
) -> WorkItemView:
    pointer = WorkRef(source=item.source, ref=item.ref)
    return WorkItemView(
        source=item.source,
        slug=slug,
        ref=item.ref,
        label=source_obj.label(pointer) or f"{slug}#{item.ref}",
        web_url=source_obj.web_url(pointer, live_holder=live_holder),
        title=item.title,
        body=item.body,
        author=WorkItemAuthorView(
            kind=author.kind,
            user_id=author.user_id,
            login=author.login,
            runner_id=author.runner_id,
            runner_name=runner_names.get(author.runner_id) if author.runner_id is not None else None,
            chunk_id=author.chunk_id,
            node_name=author.node_name,
        ),
        stated_priority=WorkItemPriority(item.stated_priority) if item.stated_priority is not None else None,
        created_at=iso_utc(item.created_at),
        edited_at=iso_utc(item.edited_at),
        closed_at=iso_utc(item.closed_at) if item.closed_at is not None else None,
        closure=item.closure,
    )


#: The old address fields a work source names its forge by.
_ADDRESSES = ("api_base", "web_base")


def _summary(
    record: ConfiguredWorkSource, forge: ConfiguredForge, services: TenantServices, names: Mapping[str, str]
) -> WorkSourceSummary:
    fields = record.fields
    return WorkSourceSummary(
        work_source_id=record.work_source_id,
        slug=record.slug,
        name=record.name,
        annotate=fields.annotate,
        edit=services.work_sources.editor(record.work_source_id) is not None,
        provider=fields.provider,
        locator=fields.locator,
        forge_id=forge.forge_id,
        forge_slug=forge.slug,
        forge_name=forge.name,
        api_base=forge.fields.api_base,
        web_base=forge.web_origin,
        secret=fields.secret,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        created_by_name=names.get(record.created_by),
        retired=record.retired,
    )


def _shown(
    record: ConfiguredWorkSource, services: TenantServices, warnings: list[OldFieldWarning] | None = None
) -> WorkSourceSummary:
    """``record``'s summary, its addresses read from its forge, with the ``warnings`` the write that left it
    answers."""
    forge = services.forge_records.get(record.fields.forge_id)
    assert forge is not None
    summary = _summary(record, forge, services, user_names(services, [record.created_by]))
    return summary.model_copy(update={"warnings": warnings or []})


def _forge_choice(request: WorkSourceCreateRequest | WorkSourcePatchRequest, services: TenantServices) -> ForgeChoice:
    """What ``request`` states of its forge: ``forge``, which is never null, with the forge it names, and the old
    address fields."""
    present = request.model_fields_set
    forge: str | UnsetType = UNSET
    if FORGE_FIELD in present:
        if request.forge is None:
            raise ConfigFieldError(FORGE_FIELD, "must not be null")
        forge = request.forge
    return ForgeChoice(
        forge=forge,
        api_base=request.api_base if "api_base" in present else UNSET,
        web_base=request.web_base if "web_base" in present else UNSET,
        named=None if forge is UNSET else services.forge_records.resolve(forge),
    )


def _built_in_summary(services: TenantServices) -> WorkSourceSummary:
    return WorkSourceSummary(
        slug=RESERVED_HUB_SOURCE_NAME,
        name=RESERVED_HUB_SOURCE_NAME,
        annotate=services.work_sources.annotator(RESERVED_HUB_SOURCE_NAME) is not None,
        edit=services.work_sources.editor(RESERVED_HUB_SOURCE_NAME) is not None,
        built_in=True,
    )


def _unprocessable(exc: ConfigFieldError) -> HTTPException:
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[{"loc": ["body", exc.field], "msg": exc.message, "type": exc.code}],
    )


def _conflict(exc: Exception) -> HTTPException:
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc))


@router.get("/work-sources", response_model=WorkSourcesListView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
def list_work_sources(services: Services, include_retired: bool = False) -> WorkSourcesListView:
    """The built-in ``hub`` source and every stored work source, retired ones hidden unless
    `include_retired`. No gate beyond `fleet:view`, since a client needs this to know which
    sources gate their items."""
    records = services.work_source_records.list_all(include_retired=include_retired)
    names = user_names(services, (r.created_by for r in records))
    forges = services.forge_records.get_many(list({r.fields.forge_id for r in records}))
    return WorkSourcesListView(
        sources=[
            _built_in_summary(services),
            *(_summary(r, forges[r.fields.forge_id], services, names) for r in records),
        ]
    )


@router.post("/work-sources", response_model=WorkSourceSummary, status_code=status.HTTP_201_CREATED)
def create_work_source(
    request: WorkSourceCreateRequest,
    response: Response,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    services: Services,
) -> WorkSourceSummary:
    """Store a new work source at revision 1 on the forge `forge` names; its `name` is its `slug` when omitted. Naming
    no `forge` is the old way — `api_base` and `web_base`, or GitHub's API when `api_base` is left out — resolved to
    the tenant's forge with that API origin, made when none holds it, and answers a `Deprecation` header and a
    `warnings` entry. 422 naming the field for a malformed `slug` or `name` (`hub` among them, the built-in source's),
    a bad provider or locator, or a missing or retired secret, with the code `forge_unavailable` for an unknown or
    retired forge or an origin a retired forge holds, `forge_kind_mismatch` for a forge of another kind than the
    provider, and `forge_conflict` for an address that is not the forge's; 409 for a slug some work source holds or a
    taken `(provider, locator)`, naming the holder."""
    draft = WorkSourceDraft(
        provider=request.provider, locator=request.locator, annotate=request.annotate, secret=request.secret
    )
    try:
        authored = services.config_authoring.create_work_source_naming(
            request.slug, request.name, draft, _forge_choice(request, services), change_context(identity, door)
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (WorkSourceSlugTaken, WorkSourceLocatorTaken, ForgeSlugTaken, ForgeOriginTaken) as exc:
        raise _conflict(exc) from exc
    record = authored.record
    return _shown(record, services, warned(response, authored.warnings))


@router.get(
    "/work-sources/{source}", response_model=WorkSourceSummary, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
def get_work_source(source: str, services: Services) -> WorkSourceSummary:
    """One work source by id or slug — a slug it left included — retired or not; the built-in `hub` shows as
    `built_in`. 404 on an unknown source."""
    if is_built_in(source):
        return _built_in_summary(services)
    return _shown(resolved_work_source(source, services), services)


@router.patch("/work-sources/{source}", response_model=WorkSourceSummary)
def patch_work_source(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathWorkSource,
    request: WorkSourcePatchRequest,
    response: Response,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> WorkSourceSummary:
    """Apply only the fields present; an explicit `null` clears `secret` (only where the provider needs no
    credential), re-points the source at GitHub's forge for `api_base`, and means the forge's own web origin for
    `web_base`, which a source no longer holds: a `web_base` other than its forge's is refused `forge_conflict`, naming
    the forge whose web origin to set instead. A patch that changes nothing writes nothing. A slug that changes keeps
    resolving to the source until another claims it. 404 unknown, 409 for `hub`, a stale `If-Match` (naming the
    current revision), a slug some work source holds, or a taken `(provider, locator)`; 422 naming the field (`hub`
    refused as a slug), coded as on create. `forge` re-points the source at a forge; `api_base` does so the old way,
    and a cleared `api_base` re-points it at GitHub's; each old field answers a `Deprecation` header and a `warnings`
    entry."""
    present = request.model_fields_set
    edit = WorkSourceEdit(
        **{name: getattr(request, name) for name in present if name not in (*_ADDRESSES, FORGE_FIELD)}
    )
    try:
        authored = services.config_authoring.edit_work_source_naming(
            record, edit, _forge_choice(request, services), change_context(identity, door), if_match=if_match
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (
        ConfigRevisionConflict,
        WorkSourceLocatorTaken,
        WorkSourceSlugTaken,
        ForgeSlugTaken,
        ForgeOriginTaken,
    ) as exc:
        raise _conflict(exc) from exc
    edited = authored.record
    return _shown(edited, services, warned(response, authored.warnings))


@router.post("/work-sources/{source}/retire", response_model=WorkSourceSummary)
def retire_work_source(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathWorkSource,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> WorkSourceSummary:
    """Retire a work source — a reversible brake that keeps its locator claim. Retiring a
    retired source changes nothing. 404 unknown, 409 for `hub` or a stale `If-Match`."""
    try:
        retired = services.config_authoring.retire_work_source(
            record, change_context(identity, door), if_match=if_match
        )
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(retired, services)


@router.post("/work-sources/{source}/enable", response_model=WorkSourceSummary)
def enable_work_source(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathWorkSource,
    door: RequestDoor,
    services: Services,
    if_match: Annotated[int | None, Header()] = None,
) -> WorkSourceSummary:
    """Re-enable a retired work source; enabling an active one changes nothing. 404 unknown,
    409 for `hub` or a stale `If-Match`, 422 when its secret has since been retired."""
    try:
        enabled = services.config_authoring.enable_work_source(
            record, change_context(identity, door), if_match=if_match
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(enabled, services)


@router.get(
    "/work-sources/{source}/items",
    response_model=WorkItemsListView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def list_work_items(
    source: PathSourceKey,
    services: Services,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> WorkItemsListView:
    """Up to LIMIT items at SOURCE, newest first, open and closed alike. 404/409 from the source's editor gate."""
    source_obj, editor = _require_editor(source, services)
    items = editor.list(limit=limit)
    holders = services.chunks.work_refs.live_holders(WorkRef(source=item.source, ref=item.ref) for item in items)
    user_ids = {
        item.author.user_id
        for item in items
        if item.author.kind is WorkItemAuthorKind.USER and item.author.user_id is not None
    }
    users_by_id = services.users.get_many(list(user_ids))
    runner_names = _runner_names(services, (item.author for item in items))
    return WorkItemsListView(
        items=[
            _view(
                item,
                source_obj,
                resolve_author_view(item.author, users_by_id),
                slug=services.work_sources.source_label(source),
                live_holder=holders.get(WorkRef(source=item.source, ref=item.ref)),
                runner_names=runner_names,
            )
            for item in items
        ]
    )


@router.post(
    "/work-sources/{source}/items",
    response_model=WorkItemCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
def create_work_item(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
    source: PathSourceKey,
    request: WorkItemCreateRequest,
    services: Services,
    project: QueryProject = None,
) -> object:
    """Allocate a fresh item at SOURCE, open, authored by the caller, and mint its
    resting ``not_ready`` chunk in the same transaction, both in the project ``?project=`` names
    — the tenant's only one when it names none. 404/409 from the source's editor gate,
    422 for a blank title or body, an unknown project, or no project named in a tenant
    holding several (``project_required``), 409 if an out-of-band ingest already holds the
    allocated ref's pointer, 503 if every graph named after the packaged default has
    been retired (the operator's brake, mirroring ``POST /chunks``)."""
    source_obj, editor = _require_editor(source, services)
    # Validated before graph resolution, so a blank title/body rejects the whole request
    # without the store consulted — the ordering POST /chunks holds for its own guard too.
    try:
        text = WorkItemText.of(title=request.title, body=request.body)
    except WorkItemFieldBlank as exc:
        raise _blank(exc) from exc
    project_id = project if project is not None else project_of_call(services).project_id
    try:
        graph = services.graph_mint.ensure_default(
            services.default_graph_doc, definition_yaml=services.default_graph_yaml
        )
    except DefaultGraphRetired as exc:
        raise HTTPException(status_code=status.HTTP_503_SERVICE_UNAVAILABLE, detail=str(exc)) from exc
    try:
        created = editor.create(
            title=text.title,
            body=text.body,
            author=WorkItemAuthor.user(identity.user_id),
            stated_priority=request.stated_priority,
            graph=graph,
            project_id=project_id,
        )
    except IngestConflict as exc:
        return ingest_conflict_response(services, exc)
    # A freshly minted chunk rests `not_ready`, exactly as a `POST /chunks` ingest does.
    chunk_events.ChunkChanged.of(services, created.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{created.chunk_id}"
    )
    services.events.publish_queue_changed()  # mint adds the chunk to the backlog list
    # The freshly minted resting chunk is always its own live holder — no read needed.
    return WorkItemCreateResponse(
        **_view(
            created.item,
            source_obj,
            resolve_author_view(created.item.author, services.users),
            slug=services.work_sources.source_label(source),
            live_holder=created.chunk_id,
            runner_names=_runner_names(services, [created.item.author]),
        ).model_dump(),
        chunk_id=created.chunk_id,
    )


@router.get(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
def get_work_item(source: PathSourceKey, ref: str, services: Services) -> WorkItemView:
    """One item at SOURCE by REF, open or closed. 404 for an unknown source, an
    unallocated ref, or a known source with no editor answered as 409."""
    source_obj, editor = _require_editor(source, services)
    pointer = WorkRef(source=source, ref=ref)
    try:
        item = editor.get(pointer)
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    return _view(
        item,
        source_obj,
        resolve_author_view(item.author, services.users),
        slug=services.work_sources.source_label(source),
        live_holder=services.chunks.work_refs.find_live_holder(pointer),
        runner_names=_runner_names(services, [item.author]),
    )


@router.patch(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
def patch_work_item(
    source: PathSourceKey,
    ref: str,
    request: WorkItemPatchRequest,
    services: Services,
) -> WorkItemView:
    """Replace the given fields in place, all-or-nothing. 404 for an unknown source or
    an unallocated ref; 409 for a known source with no editor or an item that
    already carries a closure; 422 for a blank title or body."""
    source_obj, editor = _require_editor(source, services)
    pointer = WorkRef(source=source, ref=ref)
    # Sentinel-tagged rather than merged here: filling an omitted field at the edge needs a
    # second, unguarded read of the pointer, which races a concurrent withdrawal.
    try:
        edit = WorkItemEdit(
            title=request.title if request.title is not None else UNSET,
            body=request.body if request.body is not None else UNSET,
            stated_priority=request.stated_priority if "stated_priority" in request.model_fields_set else UNSET,
        )
    except WorkItemFieldBlank as exc:
        raise _blank(exc) from exc
    try:
        updated = editor.edit(pointer, edit)
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WorkItemNotEditable as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return _view(
        updated,
        source_obj,
        resolve_author_view(updated.author, services.users),
        slug=services.work_sources.source_label(source),
        live_holder=services.chunks.work_refs.find_live_holder(pointer),
        runner_names=_runner_names(services, [updated.author]),
    )


@router.delete(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
)
def withdraw_work_item(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
    source: PathSourceKey,
    ref: str,
    services: Services,
) -> WorkItemView:
    """Withdraw the item at SOURCE/REF. 404 for an unknown source, an unallocated ref,
    or a chunk a race deletes between resolving it and this write; 409 for a known
    source with no editor, an item already closed, or one an *acquired* live chunk
    holds — an unacquired holder deletes instead unless it is now a standing
    prerequisite for another chunk, also 409."""
    source_obj, editor = _require_editor(source, services)
    pointer = WorkRef(source=source, ref=ref)
    try:
        withdrawn = editor.withdraw(pointer, by=identity.user_id)
    except WorkItemRefUnknownError as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except ChunkNotFound as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except WithdrawalLostToClosure as exc:
        _publish_holder_deletion(services, exc.deletion, by=identity.user_id)
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    except (WorkItemNotEditable, WorkItemHeldByLiveChunk, WorkItemHeldByDependents) as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    _publish_holder_deletion(services, withdrawn, by=identity.user_id)
    holder = services.chunks.work_refs.find_live_holder(pointer)
    return _view(
        withdrawn.item,
        source_obj,
        resolve_author_view(withdrawn.item.author, services.users),
        slug=services.work_sources.source_label(source),
        live_holder=holder,
        runner_names=_runner_names(services, [withdrawn.item.author]),
    )


def _publish_holder_deletion(services: TenantServices, withdrawn: WithdrawnWorkItem, *, by: str) -> None:
    """Publish the cascade-deleted holder's frames, if the withdrawal deleted one — whether
    the withdrawal then stood or was refused, the deletion has committed."""
    if withdrawn.deleted_chunk_id is None:
        return
    chunk_events.ChunkChanged.of(
        services, withdrawn.deleted_chunk_id, prev_status=withdrawn.deleted_chunk_status
    ).publish(
        cause="deleted",
        key=f"chunk_deleted:{withdrawn.deleted_chunk_fact_id}",
        by=by,
        status=withdrawn.deleted_chunk_status,
    )
    services.events.publish_queue_changed()  # a deleted chunk is never offered for claim again
