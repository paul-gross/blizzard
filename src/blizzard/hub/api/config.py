"""Configuration routes — the served document schemas, the change log, and the declarative
document: ``POST /apply`` reconciles one, ``GET /export`` writes the current one.

The schemas, the change log and the export are read-only (``FLEET_VIEW``); an apply needs
``CONFIG_EDIT`` and writes through ``ConfigAuthoring`` like every other configured-record verb,
always as ``Door.APPLY`` — the door is never read from the request (``bzh:config-apply``)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from blizzard.auth_core import CONFIG_EDIT, FLEET_VIEW
from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import HubServices
from blizzard.hub.documents.codec import ConfigDecodeError, codec_for_media_type
from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    ConfigDeclaration,
    DeclarableRecord,
    DeclaredReferences,
    EntryOutcome,
    RepositoryDeclaration,
    StoredConfig,
    WorkSourceDeclaration,
)
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange, Door, RecordKind, RecordState
from blizzard.hub.domain.config.repositories import RepositoryEdit, RepositoryFields
from blizzard.hub.domain.config.work_sources import ConfigFieldError, WorkSourceEdit, WorkSourceFields
from blizzard.hub.domain.garden.declarations import RoutineDeclaration, ScopeDeclaration
from blizzard.hub.domain.garden.routines import RoutineEdit
from blizzard.hub.domain.garden.scopes import ScopeEdit
from blizzard.wire.config import (
    ConfigApplyOutcome,
    ConfigApplyResponse,
    ConfigChangesPage,
    ConfigChangeView,
    ConfigDocument,
    FieldChangeView,
)
from blizzard.wire.repository import RepositoryDocument
from blizzard.wire.routine import RoutineDocument
from blizzard.wire.scope import ScopeDocument
from blizzard.wire.work_source import WorkSourceDocument

router = APIRouter(
    prefix="/api/config",
    tags=["config"],
    dependencies=[Depends(reject_runner_principal)],
)

#: The wire name of an outcome row for a named record that needed no write.
UNCHANGED = "unchanged"


#: Each read route gates itself, so the apply route gates on `CONFIG_EDIT` alone.
_VIEW = Depends(require(FLEET_VIEW))

#: The largest page of the change log a caller may request.
MAX_CHANGES_LIMIT = 200

_SCHEMAS = {
    "work-sources": WorkSourceDocument,
    "repositories": RepositoryDocument,
    "scopes": ScopeDocument,
    "routines": RoutineDocument,
}


def _view(change: ConfigChange) -> ConfigChangeView:
    assert change.id is not None
    return ConfigChangeView(
        id=change.id,
        recorded_at=iso_utc(change.recorded_at),
        actor=change.actor,
        door=change.door.value,
        record_kind=change.record_kind.value,
        record_key=change.record_key,
        revision=change.revision,
        op=change.op.value,
        diff=[FieldChangeView(field=d.field, old=d.old, new=d.new) for d in change.diff],
        apply_id=change.apply_id,
    )


@router.get("/schema/{kind}", dependencies=[_VIEW])
def get_schema(kind: str) -> dict[str, object]:
    """The JSON Schema of a record kind's document model; 404 for a kind with none yet."""
    model = _SCHEMAS.get(kind)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no document schema for {kind!r}")
    return model.model_json_schema()


@router.get("/changes", response_model=ConfigChangesPage, dependencies=[_VIEW])
def list_changes(
    services: Annotated[HubServices, Depends(get_services)],
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CHANGES_LIMIT)] = 50,
    record_kind: RecordKind | None = None,
    record_key: str | None = None,
) -> ConfigChangesPage:
    """The change log newest-first, keyset-paged on `id`: pass the page's `next_before` as
    `before` for the next. Optionally narrowed to one `record_kind` and `record_key`."""
    rows = services.config_changes.page(before=before, limit=limit + 1, record_kind=record_kind, record_key=record_key)
    page = rows[:limit]
    return ConfigChangesPage(changes=[_view(c) for c in page], next_before=page[-1].id if len(rows) > limit else None)


def _declaration(document: ConfigDocument, clock: IClock) -> ConfigDeclaration:
    """The domain's declarations from a validated document: each entry's create fields, and a sparse edit
    of only the fields the entry states (``model_fields_set``)."""
    sources = []
    for entry in document.work_sources:
        stated = {name: getattr(entry, name) for name in entry.model_fields_set if name != "name"}
        sources.append(
            WorkSourceDeclaration(
                name=entry.name,
                fields=WorkSourceFields(
                    provider=entry.provider,
                    locator=entry.locator,
                    api_base=entry.api_base,
                    web_base=entry.web_base,
                    annotate=entry.annotate,
                    secret=entry.secret,
                ),
                edit=WorkSourceEdit(**stated),
            )
        )
    repositories = []
    for repo in document.repositories:
        stated = {name: getattr(repo, name) for name in repo.model_fields_set if name != "name"}
        repositories.append(
            RepositoryDeclaration(
                name=repo.name,
                fields=RepositoryFields(
                    forge_api_url=repo.forge_api_url,
                    owner=repo.owner,
                    repo=repo.repo,
                    base_branch=repo.base_branch,
                    secret_name=repo.secret_name,
                ),
                edit=RepositoryEdit(**stated),
            )
        )
    scopes = [
        ScopeDeclaration(
            name=scope.slug,
            description=scope.description,
            edit=ScopeEdit(**{name: getattr(scope, name) for name in scope.model_fields_set if name != "slug"}),
        )
        for scope in document.scopes
    ]
    routines = []
    for routine in document.routines:
        stated = {name: getattr(routine, name) for name in routine.model_fields_set if name != "scopes"}
        routines.append(
            RoutineDeclaration(
                name=routine.name,
                routine_id=Id.mint(IdPrefix.ROUTINE, clock).value,
                graph_name=routine.graph_name,
                default_scope_slug=routine.default_scope_slug,
                default_model=routine.default_model,
                default_effort=routine.default_effort,
                default_harnesses=routine.default_harnesses,
                edit=RoutineEdit(**stated),
                scopes=routine.scopes if "scopes" in routine.model_fields_set else None,
            )
        )
    return ConfigDeclaration(
        secrets=tuple(document.secrets),
        work_sources=tuple(sources),
        repositories=tuple(repositories),
        scopes=tuple(scopes),
        routines=tuple(routines),
    )


def _stored(declaration: ConfigDeclaration, services: HubServices) -> StoredConfig:
    secrets = services.secret_catalog.get_many(list(declaration.secrets))
    retired = services.secret_catalog.retired_names()
    scopes: dict[str, DeclarableRecord] = {scope.slug: scope for scope in services.scopes.list_all()}
    graph_names: set[str] = set()
    names: list[str] = []
    for entry in declaration.routines:
        assert isinstance(entry, RoutineDeclaration)
        graph_names.add(entry.graph_name)
        names.append(entry.name)
    stored = services.routines.get_many_by_name(names)
    routines: dict[str, DeclarableRecord] = dict(stored)
    scope_links = services.routine_scopes.list_scopes_for([r.routine_id for r in stored.values()])
    linked = {name: tuple(scope_links[r.routine_id]) for name, r in stored.items()}
    return StoredConfig(
        work_sources=services.work_source_records.get_many([d.name for d in declaration.work_sources]),
        repositories=services.repository_records.get_many([d.name for d in declaration.repositories]),
        secrets={name: RecordState.of(name in retired) for name in secrets},
        scopes={d.name: scopes[d.name] for d in declaration.scopes if d.name in scopes},
        routines=routines,
        references=DeclaredReferences(
            scopes=frozenset(scopes) | {d.name for d in declaration.scopes},
            enabled_graphs=frozenset(n for n in graph_names if services.graphs.get_enabled_by_name(n) is not None),
            linked_scopes=linked,
        ),
    )


def _outcome(outcome: EntryOutcome) -> ConfigApplyOutcome:
    return ConfigApplyOutcome(
        kind=outcome.kind.value,
        key=outcome.key,
        op=outcome.op.value if outcome.op is not None else UNCHANGED,
        diff=[FieldChangeView(field=d.field, old=d.old, new=d.new) for d in outcome.diff],
    )


def _refusal(exc: ApplyEntryRefused) -> HTTPException:
    """422 naming the entry (and field) for an invalid entry or unavailable secret; 409 for a conflict."""
    cause = exc.cause
    if isinstance(cause, ConfigFieldError):
        loc: list[str | int] = ["body", exc.section, exc.index]
        if cause.field:
            loc.append(cause.field)
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": loc, "msg": cause.message, "type": "value_error"}],
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{exc.section}[{exc.index}]: {cause}")


def _document(body: bytes, content_type: str | None) -> ConfigDocument:
    codec = codec_for_media_type(content_type or "")
    if codec is None:
        raise HTTPException(
            status_code=status.HTTP_415_UNSUPPORTED_MEDIA_TYPE,
            detail=f"unsupported content type {content_type!r}; send a YAML or JSON document",
        )
    try:
        mapping = codec.decode(body)
    except ConfigDecodeError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {"loc": ["body"], "msg": exc.problem, "type": "value_error", "line": exc.line, "column": exc.column}
            ],
        ) from exc
    try:
        return ConfigDocument.model_validate(mapping)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", *e["loc"]], "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        ) from exc


def _apply(
    document: ConfigDocument, identity: ResolvedIdentity, services: HubServices, *, dry_run: bool
) -> ConfigApplyResponse:
    declaration = _declaration(document, services.clock)
    apply_id = None if dry_run else Id.mint(IdPrefix.CONFIG_APPLY, services.clock).value
    ctx = ChangeContext(actor=identity.user_id, door=Door.APPLY, apply_id=apply_id)
    try:
        outcomes = services.config_authoring.apply(declaration, _stored(declaration, services), ctx, dry_run=dry_run)
    except ApplyEntryRefused as exc:
        raise _refusal(exc) from exc
    return ConfigApplyResponse(dry_run=dry_run, apply_id=apply_id, outcomes=[_outcome(o) for o in outcomes])


_APPLY_BODY = {
    "required": True,
    "content": {
        media_type: {"schema": {"$ref": "#/components/schemas/ConfigDocument"}}
        for media_type in ("application/yaml", "application/json")
    },
}


@router.post("/apply", response_model=ConfigApplyResponse, openapi_extra={"requestBody": _APPLY_BODY})
async def apply_config(
    request: Request,
    identity: Annotated[ResolvedIdentity, Depends(require(CONFIG_EDIT))],
    services: Annotated[HubServices, Depends(get_services)],
    dry_run: bool = False,
) -> ConfigApplyResponse:
    """Reconcile a `version: 1` document (YAML or JSON, by `Content-Type`) in one transaction: create what is
    absent, edit only the fields an entry states, enable what is retired, leave unnamed records alone. `dry_run`
    rolls the same transaction back, with the same `outcomes` and no `apply_id`. 422 invalid, 409 conflict, 415
    unknown `Content-Type`; a refusal leaves the store unchanged."""
    document = _document(await request.body(), request.headers.get("content-type"))
    return await run_in_threadpool(_apply, document, identity, services, dry_run=dry_run)


@router.get("/export", response_model=ConfigDocument, dependencies=[_VIEW])
def export_config(services: Annotated[HubServices, Depends(get_services)]) -> ConfigDocument:
    """Every active, non-built-in work source and repository, and every active scope and routine (its linked
    scope set included), with every field, and every active secret name, as a document that applies back as a
    no-op. Retired records are left out, since applying one would enable it."""
    retired = services.secret_catalog.retired_names()
    live_routines = [r for r in sorted(services.routines.list_all(), key=lambda r: r.name) if not r.retired]
    scope_links = services.routine_scopes.list_scopes_for([r.routine_id for r in live_routines])
    sources = services.work_source_records.list_all(include_retired=False)
    repositories = services.repository_records.list_all(include_retired=False)
    return ConfigDocument(
        version=1,
        secrets=[s.name for s in services.secret_catalog.list_all() if s.name not in retired],
        work_sources=[WorkSourceDocument(name=r.name, **vars(r.fields)) for r in sources],
        repositories=[RepositoryDocument(name=r.name, **vars(r.fields)) for r in repositories],
        scopes=[
            ScopeDocument(slug=s.slug, description=s.description)
            for s in sorted(services.scopes.list_all(), key=lambda s: s.slug)
            if not s.retired
        ],
        routines=[
            RoutineDocument(
                name=r.name,
                graph_name=r.graph_name,
                default_scope_slug=r.default_scope_slug,
                default_model=r.default_model,
                default_effort=r.default_effort,
                default_harnesses=r.default_harnesses,
                scopes=scope_links[r.routine_id],
            )
            for r in live_routines
        ],
    )
