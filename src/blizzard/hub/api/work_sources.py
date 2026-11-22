"""Work-source routes — the configured-record verbs (create, list, show, patch, retire,
enable) and the operator-plane editor surface over a work source's browsable items.
The record verbs write only through
``ConfigAuthoring``; the actor and door come from the request's :class:`ChangeContext`.

Every source-addressed route is gated on the source's editor (reads included): an
unknown source is 404, a known one with no editor is 409. The sources listing itself
carries no gate; it renders each source's capability booleans instead."""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from functools import partial
from typing import Annotated

from dishka.integrations.fastapi import FromDishka, inject_sync
from fastapi import APIRouter, Depends, Header, Query, Response, status

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.foundation.work_items import WorkItemPriority
from blizzard.hub.api import chunk_events, problems, problems_admin
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.chunk_frames import ChunkFrameLoader
from blizzard.hub.api.chunk_views import ingest_conflict_refusal
from blizzard.hub.api.credential_choice import credential_choice
from blizzard.hub.api.door import RequestDoor, change_context
from blizzard.hub.api.forge_choice import forge_choice
from blizzard.hub.api.forge_warnings import OldFieldWarnings, Warnings
from blizzard.hub.api.problem_exceptions import ProblemHTTPException
from blizzard.hub.api.project_params import QueryProject, project_of_call
from blizzard.hub.api.record_reads import Reads, RecordReads
from blizzard.hub.api.urgencies import stated_urgency, urgency_refusal
from blizzard.hub.api.user_names import user_names
from blizzard.hub.api.work_source_params import PathSourceKey, PathWorkSource, resolved_work_source
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.users import IReadUserRepository
from blizzard.hub.documents import YAML_CODEC
from blizzard.hub.domain.chunk.errors import ChunkNotFound
from blizzard.hub.domain.chunk.ingest import IngestConflict
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor, WorkItemAuthorKind, WorkRef
from blizzard.hub.domain.chunk.ports.work_refs import IReadChunkWorkRefsRepository
from blizzard.hub.domain.config.authoring import Authored, ConfigAuthoring
from blizzard.hub.domain.config.credential_naming import CREDENTIAL_FIELD
from blizzard.hub.domain.config.credentials import ConfiguredCredential
from blizzard.hub.domain.config.errors import ConfigFieldError, ConfigRevisionConflict
from blizzard.hub.domain.config.forge_resolution import FORGE_FIELD
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    IReadWorkSourceRepository,
    WorkSourceDraft,
    WorkSourceEdit,
    WorkSourceLocatorTaken,
    WorkSourceSlugTaken,
    is_built_in,
)
from blizzard.hub.domain.graph.authoring import DefaultGraphRetired, DefaultGraphService
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.projects.model import IReadProjectRepository
from blizzard.hub.domain.runners.registration import IReadRunnerRegistry
from blizzard.hub.domain.urgency.model import UrgencyRetired, UrgencyUnknown
from blizzard.hub.domain.urgency.services import UrgencyReading
from blizzard.hub.domain.work_items.editing import (
    WithdrawalLostToClosure,
    WithdrawnWorkItem,
    WorkItemHeldByDependents,
    WorkItemHeldByLiveChunk,
)
from blizzard.hub.domain.work_items.model import WorkItemEdit, WorkItemFieldBlank, WorkItemNotEditable, WorkItemText
from blizzard.hub.graphs import PACKAGED
from blizzard.hub.work_sources.editor import IWorkEditor, WorkItemRefUnknownError
from blizzard.hub.work_sources.registry import label_from_binding
from blizzard.hub.work_sources.source import AuthorView, IWorkSource, IWorkSourceRegistry, resolve_author_view
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
#: The tenant's work-source registry and records, config writer, holder, runner and user reads, and chunk frames.
Sources = FromDishka[IWorkSourceRegistry]
SourceRecords = FromDishka[IReadWorkSourceRepository]
Authoring = FromDishka[ConfigAuthoring]
Holders = FromDishka[IReadChunkWorkRefsRepository]
Runners = FromDishka[IReadRunnerRegistry]
Users = FromDishka[IReadUserRepository]
Frames = FromDishka[ChunkFrameLoader]


def _require_editor(source: str, sources: IWorkSourceRegistry) -> tuple[IWorkSource, IWorkEditor]:
    """The source keyed ``source`` and its editor, or the refusal: 404 for an unknown source,
    409 for a known one with no editor — a structural refusal for every source but
    ``hub``, since no work-source record field could ever opt a configured source into
    editing (``blizzard-context:/architecture/system-shape.md``), not merely "not opted
    in" the way ``annotate`` is."""
    source_obj = sources.get(source)
    if source_obj is None:
        raise ProblemHTTPException(
            partial(problems_admin.work_source_not_found_problem, f"unknown work source {source!r}")
        )
    editor = sources.editor(source)
    if editor is None:
        raise ProblemHTTPException(
            partial(
                problems_admin.work_source_no_editor_problem,
                f"work source {sources.source_label(source)!r} has no editor",
            )
        )
    return source_obj, editor


def _blank(exc: WorkItemFieldBlank) -> ProblemHTTPException:
    return ProblemHTTPException(partial(problems_admin.work_item_field_blank_problem, str(exc)))


def _runner_names(runners: IReadRunnerRegistry, authors: Iterable[WorkItemAuthor]) -> dict[str, str]:
    """The registered names of the fleet authors among ``authors`` — one batched registry read,
    none at all when every author is a person."""
    return runners.names_for(author.runner_id for author in authors if author.runner_id is not None)


def _view(
    item: HubWorkItem,
    source_obj: IWorkSource,
    author: AuthorView,
    *,
    slug: str,
    live_holder: str | None,
    runner_names: Mapping[str, str],
    label: str,
) -> WorkItemView:
    pointer = WorkRef(source=item.source, ref=item.ref)
    return WorkItemView(
        source=item.source,
        slug=slug,
        ref=item.ref,
        label=label,
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


def _summary(
    record: ConfiguredWorkSource,
    forge: ConfiguredForge,
    credential: ConfiguredCredential | None,
    sources: IWorkSourceRegistry,
    names: Mapping[str, str],
) -> WorkSourceSummary:
    fields = record.fields
    return WorkSourceSummary(
        work_source_id=record.work_source_id,
        slug=record.slug,
        name=record.name,
        annotate=fields.annotate,
        edit=sources.editor(record.work_source_id) is not None,
        provider=fields.provider,
        locator=fields.locator,
        forge_id=forge.forge_id,
        forge_slug=forge.slug,
        forge_name=forge.name,
        secret=fields.secret,
        revision=record.revision,
        created_at=iso_utc(record.created_at),
        created_by=record.created_by,
        created_by_name=names.get(record.created_by),
        retired=record.retired,
        credential_id=None if credential is None else credential.credential_id,
        credential_slug=None if credential is None else credential.slug,
        credential_name=None if credential is None else credential.name,
    )


def _shown(record: ConfiguredWorkSource, sources: IWorkSourceRegistry, reads: RecordReads) -> WorkSourceSummary:
    """``record``'s summary, with the forge and credential it names."""
    forge = reads.forges.get(record.fields.forge_id)
    assert forge is not None
    credential_id = record.fields.credential_id
    credential = None if credential_id is None else reads.credentials.get(credential_id)
    return _summary(record, forge, credential, sources, user_names(reads.users, [record.created_by]))


def _authored(
    authored: Authored[ConfiguredWorkSource],
    response: Response,
    sources: IWorkSourceRegistry,
    reads: RecordReads,
    warnings: OldFieldWarnings,
) -> WorkSourceSummary:
    """The written record's summary, carrying a warning for each old field the write resolved."""
    shown = _shown(authored.record, sources, reads)
    forge = reads.forges.get(authored.record.fields.forge_id)
    assert forge is not None
    shown.warnings = warnings.warned(response, authored.old_fields, record=f"work source {shown.slug}", forge=forge)
    return shown


def _built_in_summary(sources: IWorkSourceRegistry) -> WorkSourceSummary:
    return WorkSourceSummary(
        slug=RESERVED_HUB_SOURCE_NAME,
        name=RESERVED_HUB_SOURCE_NAME,
        annotate=sources.annotator(RESERVED_HUB_SOURCE_NAME) is not None,
        edit=sources.editor(RESERVED_HUB_SOURCE_NAME) is not None,
        built_in=True,
    )


def _unprocessable(exc: ConfigFieldError) -> ProblemHTTPException:
    return ProblemHTTPException(
        partial(problems_admin.config_field_invalid_problem, "body", exc.field, exc.message, exc.code)
    )


def _conflict(exc: Exception) -> ProblemHTTPException:
    if isinstance(exc, WorkSourceSlugTaken):
        return ProblemHTTPException(partial(problems_admin.work_source_slug_taken_problem, str(exc)))
    if isinstance(exc, WorkSourceLocatorTaken):
        return ProblemHTTPException(partial(problems_admin.work_source_locator_taken_problem, str(exc)))
    return ProblemHTTPException(partial(problems.revision_conflict_problem, str(exc)))


@router.get("/work-sources", response_model=WorkSourcesListView, dependencies=[Depends(require(Permission.FLEET_VIEW))])
@inject_sync
def list_work_sources(
    sources: Sources, source_records: SourceRecords, reads: Reads, include_retired: bool = False
) -> WorkSourcesListView:
    """The built-in ``hub`` source and every stored work source, retired ones hidden unless
    `include_retired`. No gate beyond `fleet:view`, since a client needs this to know which
    sources gate their items."""
    stored = source_records.list_all(include_retired=include_retired)
    names = user_names(reads.users, (r.created_by for r in stored))
    forges = reads.forges.get_many(list({r.fields.forge_id for r in stored}))
    credentials = reads.credentials.get_many(
        list({r.fields.credential_id for r in stored if r.fields.credential_id is not None})
    )
    return WorkSourcesListView(
        sources=[
            _built_in_summary(sources),
            *(
                _summary(
                    r,
                    forges[r.fields.forge_id],
                    None if r.fields.credential_id is None else credentials.get(r.fields.credential_id),
                    sources,
                    names,
                )
                for r in stored
            ),
        ]
    )


@router.post("/work-sources", response_model=WorkSourceSummary, status_code=status.HTTP_201_CREATED)
@inject_sync
def create_work_source(
    request: WorkSourceCreateRequest,
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    door: RequestDoor,
    sources: Sources,
    authoring: Authoring,
    reads: Reads,
    warnings: Warnings,
    response: Response,
) -> WorkSourceSummary:
    """Store a new work source at revision 1 on the forge `forge` names; its `name` is its `slug` when omitted. 422
    naming the field for a malformed `slug` or `name` (`hub` among them, the built-in source's), a bad provider or
    locator, or a missing or retired secret, with the code `forge_required` for no `forge`, `forge_unavailable` for an
    unknown or retired forge, `forge_kind_mismatch` for a forge of another kind than the provider, and
    `field_moved_to_forge` for an `api_base` or `web_base`, which the forge carries — naming that forge where one can
    be told; 409 for a slug some work source holds or a taken `(provider, locator)`, naming the holder.

    The source acts with the credential `credential` names, or the `github.token` credential reading the deprecated
    `secret` on the forge — created when there is none, and answered with a `Deprecation` header and a `warnings`
    entry; a provider that acts with a credential needs one of them. 422 coded `credential_unavailable` for an unknown
    or retired credential, `credential_forge_mismatch` for one of another forge, `credential_binding_unsupported` for
    a binding the provider cannot act with, and `credential_conflict` for a `secret` that is not the credential's
    secret."""
    draft = WorkSourceDraft(
        provider=request.provider, locator=request.locator, annotate=request.annotate, secret=request.secret
    )
    try:
        authored = authoring.create_work_source_naming(
            request.slug,
            request.name,
            draft,
            forge_choice(request, reads.forges),
            change_context(identity, door),
            credential=credential_choice(request, reads.credentials),
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (WorkSourceSlugTaken, WorkSourceLocatorTaken) as exc:
        raise _conflict(exc) from exc
    return _authored(authored, response, sources, reads, warnings)


@router.get(
    "/work-sources/{source}", response_model=WorkSourceSummary, dependencies=[Depends(require(Permission.FLEET_VIEW))]
)
@inject_sync
def get_work_source(source: str, sources: Sources, source_records: SourceRecords, reads: Reads) -> WorkSourceSummary:
    """One work source by id or slug — a slug it left included — retired or not; the built-in `hub` shows as
    `built_in`. 404 on an unknown source."""
    if is_built_in(source):
        return _built_in_summary(sources)
    return _shown(resolved_work_source(source, source_records), sources, reads)


@router.patch("/work-sources/{source}", response_model=WorkSourceSummary)
@inject_sync
def patch_work_source(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathWorkSource,
    request: WorkSourcePatchRequest,
    door: RequestDoor,
    sources: Sources,
    authoring: Authoring,
    reads: Reads,
    warnings: Warnings,
    response: Response,
    if_match: Annotated[int | None, Header()] = None,
) -> WorkSourceSummary:
    """Apply only the fields present; an explicit `null` clears `secret`, and `credential` with it, only where the
    provider needs no credential; on any other provider a `null` `credential` is a 422 naming it. A patch that changes
    nothing writes nothing. A slug that changes keeps resolving to the source until another claims it. 404 unknown, 409
    for `hub`, a stale `If-Match` (naming the current revision), a slug some work source holds, or a taken `(provider,
    locator)`; 422 naming the field (`hub` refused as a slug), coded as on create, a `field_moved_to_forge` naming the
    source's own forge when the patch names none. `forge` re-points the source at a forge, and `credential` at a
    credential, resolved and refused as on create; a deprecated `secret` other than the source's resolves as on create.
    Moving the forge, or changing `secret`, of a source acting with a credential needs a `credential` too: 422
    `credential_forge_mismatch` or `credential_conflict` without one."""
    present = request.model_fields_set
    edit = WorkSourceEdit(
        **{name: getattr(request, name) for name in present if name not in (FORGE_FIELD, CREDENTIAL_FIELD)}
    )
    try:
        authored = authoring.edit_work_source_naming(
            record,
            edit,
            forge_choice(request, reads.forges),
            change_context(identity, door),
            if_match=if_match,
            credential=credential_choice(request, reads.credentials),
        )
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except (ConfigRevisionConflict, WorkSourceLocatorTaken, WorkSourceSlugTaken) as exc:
        raise _conflict(exc) from exc
    return _authored(authored, response, sources, reads, warnings)


@router.post("/work-sources/{source}/retire", response_model=WorkSourceSummary)
@inject_sync
def retire_work_source(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathWorkSource,
    door: RequestDoor,
    sources: Sources,
    authoring: Authoring,
    reads: Reads,
    if_match: Annotated[int | None, Header()] = None,
) -> WorkSourceSummary:
    """Retire a work source — a reversible brake that keeps its locator claim. Retiring a
    retired source changes nothing. 404 unknown, 409 for `hub` or a stale `If-Match`."""
    try:
        retired = authoring.retire_work_source(record, change_context(identity, door), if_match=if_match)
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(retired, sources, reads)


@router.post("/work-sources/{source}/enable", response_model=WorkSourceSummary)
@inject_sync
def enable_work_source(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    record: PathWorkSource,
    door: RequestDoor,
    sources: Sources,
    authoring: Authoring,
    reads: Reads,
    if_match: Annotated[int | None, Header()] = None,
) -> WorkSourceSummary:
    """Re-enable a retired work source; enabling an active one changes nothing. 404 unknown,
    409 for `hub` or a stale `If-Match`, 422 when its secret has since been retired."""
    try:
        enabled = authoring.enable_work_source(record, change_context(identity, door), if_match=if_match)
    except ConfigFieldError as exc:
        raise _unprocessable(exc) from exc
    except ConfigRevisionConflict as exc:
        raise _conflict(exc) from exc
    return _shown(enabled, sources, reads)


@router.get(
    "/work-sources/{source}/items",
    response_model=WorkItemsListView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
@inject_sync
def list_work_items(
    source: PathSourceKey,
    sources: Sources,
    holders: Holders,
    runners: Runners,
    users: Users,
    limit: Annotated[int, Query(ge=1, le=1000)] = 200,
) -> WorkItemsListView:
    """Up to LIMIT items at SOURCE, newest first, open and closed alike. 404/409 from the source's editor gate."""
    source_obj, editor = _require_editor(source, sources)
    items = editor.list(limit=limit)
    held = holders.live_holders(WorkRef(source=item.source, ref=item.ref) for item in items)
    user_ids = {
        item.author.user_id
        for item in items
        if item.author.kind is WorkItemAuthorKind.USER and item.author.user_id is not None
    }
    users_by_id = users.get_many(list(user_ids))
    runner_names = _runner_names(runners, (item.author for item in items))
    slug = sources.source_label(source) if items else source
    return WorkItemsListView(
        items=[
            _view(
                item,
                source_obj,
                resolve_author_view(item.author, users_by_id),
                slug=slug,
                live_holder=held.get(WorkRef(source=item.source, ref=item.ref)),
                runner_names=runner_names,
                label=label_from_binding(WorkRef(source=item.source, ref=item.ref), source_obj, lambda: slug),
            )
            for item in items
        ]
    )


@router.post(
    "/work-sources/{source}/items",
    response_model=WorkItemCreateResponse,
    status_code=status.HTTP_201_CREATED,
)
@inject_sync
def create_work_item(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
    source: PathSourceKey,
    request: WorkItemCreateRequest,
    sources: Sources,
    urgencies: FromDishka[UrgencyReading],
    projects: FromDishka[IReadProjectRepository],
    default_graph: FromDishka[DefaultGraphService],
    frames: Frames,
    runners: Runners,
    users: Users,
    project: QueryProject = None,
) -> object:
    """Allocate an open item at SOURCE and mint its resting ``not_ready`` chunk in one transaction, in the project
    ``?project=`` names or the tenant's only one. 404/409 from the editor gate; 422 for a blank title or body,
    ``urgency_unknown``/``urgency_retired``, an unknown project, or ``project_required``; 409 if an out-of-band
    ingest holds the allocated pointer; 503 if every default-named graph is retired (mirroring ``POST /chunks``)."""
    source_obj, editor = _require_editor(source, sources)
    # Validated before graph resolution, so a blank title/body rejects the whole request
    # without the store consulted — the ordering POST /chunks holds for its own guard too.
    try:
        text = WorkItemText.of(title=request.title, body=request.body)
    except WorkItemFieldBlank as exc:
        raise _blank(exc) from exc
    urgency = stated_urgency(request.urgency, urgencies)
    project_id = project if project is not None else project_of_call(projects).project_id
    try:
        graph = default_graph.ensure_default(
            PACKAGED.default.doc, definition_yaml=PACKAGED.default.inlined_yaml(YAML_CODEC)
        )
    except DefaultGraphRetired as exc:
        raise ProblemHTTPException(partial(problems_admin.default_graph_retired_problem, str(exc))) from exc
    try:
        created = editor.create(
            title=text.title,
            body=text.body,
            author=WorkItemAuthor.user(identity.user_id),
            stated_priority=request.stated_priority,
            graph=graph,
            project_id=project_id,
            urgency=urgency,
        )
    except (UrgencyUnknown, UrgencyRetired) as exc:  # retired after the edge's check
        raise urgency_refusal(exc) from exc
    except IngestConflict as exc:
        raise ingest_conflict_refusal(sources, exc) from exc
    # A freshly minted chunk rests `not_ready`, exactly as a `POST /chunks` ingest does.
    chunk_events.ChunkChanged.of(frames, created.chunk_id, prev_status=None).publish(
        cause="minted", key=f"chunks:{created.chunk_id}"
    )
    frames.events.publish_queue_changed()  # mint adds the chunk to the backlog list
    # The freshly minted resting chunk is always its own live holder — no read needed.
    return WorkItemCreateResponse(
        **_view(
            created.item,
            source_obj,
            resolve_author_view(created.item.author, users),
            slug=sources.source_label(source),
            live_holder=created.chunk_id,
            runner_names=_runner_names(runners, [created.item.author]),
            label=sources.label(WorkRef(source=created.item.source, ref=created.item.ref)),
        ).model_dump(),
        chunk_id=created.chunk_id,
    )


@router.get(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(Permission.FLEET_VIEW))],
)
@inject_sync
def get_work_item(
    source: PathSourceKey, ref: str, sources: Sources, holders: Holders, runners: Runners, users: Users
) -> WorkItemView:
    """One item at SOURCE by REF, open or closed. 404 for an unknown source, an
    unallocated ref, or a known source with no editor answered as 409."""
    source_obj, editor = _require_editor(source, sources)
    pointer = WorkRef(source=source, ref=ref)
    try:
        item = editor.get(pointer)
    except WorkItemRefUnknownError as exc:
        raise ProblemHTTPException(partial(problems_admin.work_item_not_found_problem, str(exc))) from exc
    return _view(
        item,
        source_obj,
        resolve_author_view(item.author, users),
        slug=sources.source_label(source),
        live_holder=holders.find_live_holder(pointer),
        runner_names=_runner_names(runners, [item.author]),
        label=sources.label(pointer),
    )


@router.patch(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
    dependencies=[Depends(require(Permission.CHUNK_CONTROL))],
)
@inject_sync
def patch_work_item(
    source: PathSourceKey,
    ref: str,
    request: WorkItemPatchRequest,
    sources: Sources,
    holders: Holders,
    runners: Runners,
    users: Users,
) -> WorkItemView:
    """Replace the given fields in place, all-or-nothing. 404 for an unknown source or
    an unallocated ref; 409 for a known source with no editor or an item that
    already carries a closure; 422 for a blank title or body."""
    source_obj, editor = _require_editor(source, sources)
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
        raise ProblemHTTPException(partial(problems_admin.work_item_not_found_problem, str(exc))) from exc
    except WorkItemNotEditable as exc:
        raise ProblemHTTPException(partial(problems_admin.work_item_not_editable_problem, str(exc))) from exc
    return _view(
        updated,
        source_obj,
        resolve_author_view(updated.author, users),
        slug=sources.source_label(source),
        live_holder=holders.find_live_holder(pointer),
        runner_names=_runner_names(runners, [updated.author]),
        label=sources.label(pointer),
    )


@router.delete(
    "/work-sources/{source}/items/{ref}",
    response_model=WorkItemView,
)
@inject_sync
def withdraw_work_item(
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CHUNK_CONTROL))],
    source: PathSourceKey,
    ref: str,
    sources: Sources,
    holders: Holders,
    runners: Runners,
    users: Users,
    frames: Frames,
) -> WorkItemView:
    """Withdraw the item at SOURCE/REF. 404 for an unknown source, an unallocated ref,
    or a chunk a race deletes between resolving it and this write; 409 for a known
    source with no editor, an item already closed, or one an *acquired* live chunk
    holds — an unacquired holder deletes instead unless it is now a standing
    prerequisite for another chunk, also 409."""
    source_obj, editor = _require_editor(source, sources)
    pointer = WorkRef(source=source, ref=ref)
    try:
        withdrawn = editor.withdraw(pointer, by=identity.user_id)
    except WorkItemRefUnknownError as exc:
        raise ProblemHTTPException(partial(problems_admin.work_item_not_found_problem, str(exc))) from exc
    except ChunkNotFound as exc:
        raise ProblemHTTPException(partial(problems.chunk_disappeared_problem, exc)) from exc
    except WithdrawalLostToClosure as exc:
        _publish_holder_deletion(frames, exc.deletion, by=identity.user_id)
        raise ProblemHTTPException(partial(problems_admin.work_item_not_editable_problem, str(exc))) from exc
    except WorkItemNotEditable as exc:
        raise ProblemHTTPException(partial(problems_admin.work_item_not_editable_problem, str(exc))) from exc
    except WorkItemHeldByLiveChunk as exc:
        raise ProblemHTTPException(partial(problems_admin.work_item_held_by_live_chunk_problem, str(exc))) from exc
    except WorkItemHeldByDependents as exc:
        raise ProblemHTTPException(partial(problems_admin.work_item_held_by_dependents_problem, str(exc))) from exc
    _publish_holder_deletion(frames, withdrawn, by=identity.user_id)
    holder = holders.find_live_holder(pointer)
    return _view(
        withdrawn.item,
        source_obj,
        resolve_author_view(withdrawn.item.author, users),
        slug=sources.source_label(source),
        live_holder=holder,
        runner_names=_runner_names(runners, [withdrawn.item.author]),
        label=sources.label(pointer),
    )


def _publish_holder_deletion(frames: ChunkFrameLoader, withdrawn: WithdrawnWorkItem, *, by: str) -> None:
    """Publish the cascade-deleted holder's frames, if the withdrawal deleted one — whether
    the withdrawal then stood or was refused, the deletion has committed."""
    if withdrawn.deleted_chunk_id is None:
        return
    chunk_events.ChunkChanged.of(
        frames, withdrawn.deleted_chunk_id, prev_status=withdrawn.deleted_chunk_status
    ).publish(
        cause="deleted",
        key=f"chunk_deleted:{withdrawn.deleted_chunk_fact_id}",
        by=by,
        status=withdrawn.deleted_chunk_status,
    )
    frames.events.publish_queue_changed()  # a deleted chunk is never offered for claim again
