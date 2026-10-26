"""Configuration routes — the served document schemas, the change log, and the declarative
document: ``POST /apply`` reconciles one, ``GET /export`` writes the current one.

The schemas, the change log and the export are read-only (``Permission.FLEET_VIEW``); an apply needs
``Permission.CONFIG_EDIT`` and writes through ``ConfigAuthoring`` like every other configured-record verb,
always as ``Door.APPLY`` — the door is never read from the request (``bzh:config-apply``)."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.project_params import PROJECT_REQUIRED, QueryProject
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.documents import ConfigDecodeError, codec_for_media_type
from blizzard.hub.domain.config.apply import ApplyEntryRefused, EntryOutcome
from blizzard.hub.domain.config.changes import ConfigChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import ForgeId
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryEdit, RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceEdit, WorkSourceId
from blizzard.hub.domain.config_apply.entries import (
    DeclaredRepository,
    DeclaredRoutine,
    DeclaredScope,
    DeclaredWorkSource,
    DocumentEntries,
)
from blizzard.hub.domain.garden.routines import RoutineEdit
from blizzard.hub.domain.garden.scopes import ScopeEdit
from blizzard.hub.domain.kernel.unset import UNSET
from blizzard.hub.domain.projects.declarations import EntryProjectRequired, ProjectDeclaration
from blizzard.hub.domain.projects.model import ProjectEdit, ProjectId
from blizzard.wire.config import (
    ConfigApplyOutcome,
    ConfigApplyResponse,
    ConfigChangesPage,
    ConfigChangeView,
    ConfigDocument,
    FieldChangeView,
    ProjectDocument,
    RoutineEntry,
    ScopeEntry,
)
from blizzard.wire.repository import RepositoryDocument
from blizzard.wire.routine import RoutineDocument
from blizzard.wire.scope import ScopeDocument
from blizzard.wire.work_source import WorkSourceDocument

router = APIRouter(
    prefix="/api/config",
    tags=["config"],
    dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)],
)

#: The wire name of an outcome row for a named record that needed no write.
UNCHANGED = "unchanged"


#: Each read route gates itself, so the apply route gates on `Permission.CONFIG_EDIT` alone.
_VIEW = Depends(require(Permission.FLEET_VIEW))

#: The largest page of the change log a caller may request.
MAX_CHANGES_LIMIT = 200

_SCHEMAS = {
    "work-sources": WorkSourceDocument,
    "repositories": RepositoryDocument,
    "scopes": ScopeDocument,
    "routines": RoutineDocument,
}


#: The kinds whose ``record_key`` is a repository's id, those whose is a work source's, and the kinds whose is a
#: forge's and a project's.
_REPOSITORY_KEYED = (RecordKind.REPOSITORY, RecordKind.PROJECT_REPOSITORY_LINK)
_WORK_SOURCE_KEYED = (RecordKind.WORK_SOURCE, RecordKind.PROJECT_SOURCE_LINK)
_FORGE_KEYED = (RecordKind.FORGE,)

#: The old address fields of each document entry, read into what the entry says of its forge.
_REPOSITORY_ADDRESS = "forge_api_url"
_WORK_SOURCE_ADDRESSES = ("api_base", "web_base")


def _labels(changes: list[ConfigChange], services: TenantServices) -> dict[tuple[RecordKind, str], str]:
    """The current slug of each slugged record a page of changes names: one read each of the repositories, the work
    sources, the forges and the projects it keys (``bzh:bulk-reconstitution``)."""
    repository_ids = list({RepositoryId(c.record_key) for c in changes if c.record_kind in _REPOSITORY_KEYED})
    source_ids = list({WorkSourceId(c.record_key) for c in changes if c.record_kind in _WORK_SOURCE_KEYED})
    forge_ids = list({ForgeId(c.record_key) for c in changes if c.record_kind in _FORGE_KEYED})
    project_ids = list({ProjectId(c.record_key) for c in changes if c.record_kind is RecordKind.PROJECT})
    labels: dict[tuple[RecordKind, str], str] = {}
    for repository_id, repository in services.repository_records.get_many(repository_ids).items():
        labels.update({(kind, repository_id): repository.slug for kind in _REPOSITORY_KEYED})
    for source_id, source in services.work_source_records.get_many(source_ids).items():
        labels.update({(kind, source_id): source.slug for kind in _WORK_SOURCE_KEYED})
    for forge_id, forge in services.forge_records.get_many(forge_ids).items():
        labels.update({(kind, forge_id): forge.slug for kind in _FORGE_KEYED})
    for project_id, project in services.projects.get_many(project_ids).items():
        labels[(RecordKind.PROJECT, project_id)] = project.slug
    return labels


def _actor_names(changes: list[ConfigChange], services: TenantServices) -> dict[str, str]:
    """The display name — else the username — of each user actor a page of changes names, in one read
    (``bzh:bulk-reconstitution``); an actor that is no user is absent."""
    return user_names(services, (c.actor for c in changes))


def _view(change: ConfigChange, labels: dict[tuple[RecordKind, str], str], names: dict[str, str]) -> ConfigChangeView:
    assert change.id is not None
    return ConfigChangeView(
        id=change.id,
        recorded_at=iso_utc(change.recorded_at),
        actor=change.actor,
        actor_name=names.get(change.actor),
        door=change.door.value,
        record_kind=change.record_kind.value,
        record_key=change.record_key,
        record_label=labels.get((change.record_kind, change.record_key)),
        revision=change.revision,
        op=change.op.value,
        diff=[FieldChangeView(field=d.field, old=d.old, new=d.new) for d in change.diff],
        apply_id=change.apply_id,
        project_id=change.project_id,
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
    services: Services,
    before: Annotated[int | None, Query(ge=1)] = None,
    limit: Annotated[int, Query(ge=1, le=MAX_CHANGES_LIMIT)] = 50,
    record_kind: RecordKind | None = None,
    record_key: str | None = None,
    project: QueryProject = None,
) -> ConfigChangesPage:
    """The change log newest-first, keyset-paged on `id`: pass the page's `next_before` as
    `before` for the next. Optionally narrowed to one `record_kind` and `record_key` — for a project or a repository
    the record's id or slug — and to the changes concerning one `project` (its id or slug; 422 when the tenant holds
    no such project)."""
    rows = services.config_changes.page(
        before=before,
        limit=limit + 1,
        record_kind=record_kind,
        record_key=_key_of(record_kind, record_key, services),
        project_id=project,
    )
    page = rows[:limit]
    labels = _labels(page, services)
    names = _actor_names(page, services)
    return ConfigChangesPage(
        changes=[_view(c, labels, names) for c in page],
        next_before=page[-1].id if len(rows) > limit else None,
        total=services.config_changes.count(
            record_kind=record_kind, record_key=_key_of(record_kind, record_key, services), project_id=project
        ),
    )


def _key_of(kind: RecordKind | None, key: str | None, services: TenantServices) -> str | None:
    """The stored key a change-log filter names: the id of the project, repository, work source or forge an id or slug
    resolves to; the
    value as given when it resolves to none (so the page is empty) or the kind is keyed by its name."""
    if key is None:
        return None
    if kind is RecordKind.PROJECT:
        found = services.projects.resolve(key)
        return key if found is None else found.project_id
    if kind in _REPOSITORY_KEYED:
        repository = services.repository_records.resolve(key)
        return key if repository is None else repository.repository_id
    if kind in _WORK_SOURCE_KEYED:
        source = services.work_source_records.resolve(key)
        return key if source is None else source.work_source_id
    if kind in _FORGE_KEYED:
        forge = services.forge_records.resolve(key)
        return key if forge is None else forge.forge_id
    return key


def _entries(document: ConfigDocument) -> DocumentEntries:
    """The domain's entries from a validated document: each entry's create fields, a sparse edit of only the
    fields the entry states (``model_fields_set``), and what it says of its forge — the old address fields it states."""
    sources = []
    for entry in document.work_sources:
        stated = {name: getattr(entry, name) for name in entry.model_fields_set if name != "slug"}
        addresses = {name: stated.pop(name) for name in _WORK_SOURCE_ADDRESSES if name in stated}
        sources.append(
            DeclaredWorkSource(
                slug=entry.slug,
                name=entry.name,
                draft=WorkSourceDraft(
                    provider=entry.provider, locator=entry.locator, annotate=entry.annotate, secret=entry.secret
                ),
                edit=WorkSourceEdit(**stated),
                forge=ForgeChoice(**addresses),
            )
        )
    repositories = []
    for repo in document.repositories:
        stated = {name: getattr(repo, name) for name in repo.model_fields_set if name != "slug"}
        address = stated.pop(_REPOSITORY_ADDRESS, UNSET)
        repositories.append(
            DeclaredRepository(
                slug=repo.slug,
                name=repo.name,
                draft=RepositoryDraft(
                    owner=repo.owner, repo=repo.repo, base_branch=repo.base_branch, secret_name=repo.secret_name
                ),
                edit=RepositoryEdit(**stated),
                forge=ForgeChoice(api_base=address),
            )
        )
    scopes = [
        DeclaredScope(
            name=scope.slug,
            description=scope.description,
            edit=ScopeEdit(
                **{name: getattr(scope, name) for name in scope.model_fields_set if name not in ("slug", "project")}
            ),
            project=scope.project,
        )
        for scope in document.scopes
    ]
    routines = []
    for routine in document.routines:
        stated = {
            name: getattr(routine, name) for name in routine.model_fields_set if name not in ("scopes", "project")
        }
        routines.append(
            DeclaredRoutine(
                name=routine.name,
                graph_name=routine.graph_name,
                default_scope_slug=routine.default_scope_slug,
                default_model=routine.default_model,
                default_effort=routine.default_effort,
                default_harnesses=routine.default_harnesses,
                edit=RoutineEdit(**stated),
                project=routine.project,
                scopes=routine.scopes if "scopes" in routine.model_fields_set else None,
            )
        )
    return DocumentEntries(
        secrets=tuple(document.secrets),
        work_sources=tuple(sources),
        repositories=tuple(repositories),
        projects=tuple(_project_declaration(entry) for entry in document.projects),
        scopes=tuple(scopes),
        routines=tuple(routines),
    )


def _project_declaration(entry: ProjectDocument) -> ProjectDeclaration:
    stated = entry.model_fields_set
    edit: dict[str, Any] = {}
    if "name" in stated and entry.name is not None:
        edit["name"] = entry.name
    if "description" in stated:
        edit["description"] = entry.description
    if "color" in stated:
        edit["color"] = entry.color
    return ProjectDeclaration(
        slug=entry.slug,
        edit=ProjectEdit(**edit),
        sources=tuple(entry.sources) if "sources" in stated else None,
        repositories=tuple(entry.repositories) if "repositories" in stated else None,
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
    if isinstance(cause, EntryProjectRequired):
        return HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[
                {
                    "loc": ["body", exc.section, exc.index, cause.field],
                    "msg": cause.message,
                    "type": PROJECT_REQUIRED,
                    "projects": [
                        {"project_id": project.project_id, "slug": project.slug, "name": project.name}
                        for project in cause.projects
                    ],
                }
            ],
        )
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
    document: ConfigDocument, identity: ResolvedIdentity, services: TenantServices, *, dry_run: bool
) -> ConfigApplyResponse:
    try:
        applied = services.config_application.apply(_entries(document), actor=identity.user_id, dry_run=dry_run)
    except ApplyEntryRefused as exc:
        raise _refusal(exc) from exc
    return ConfigApplyResponse(
        dry_run=dry_run, apply_id=applied.apply_id, outcomes=[_outcome(o) for o in applied.outcomes]
    )


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
    identity: Annotated[ResolvedIdentity, Depends(require(Permission.CONFIG_EDIT))],
    services: Services,
    dry_run: bool = False,
) -> ConfigApplyResponse:
    """Reconcile a `version: 1` document (YAML or JSON, by `Content-Type`) in one transaction: create what is
    absent, edit only the fields an entry states, enable what is retired, leave unnamed records alone. `dry_run`
    rolls the same transaction back, with the same `outcomes` and no `apply_id`. 422 invalid, 409 conflict, 415
    unknown `Content-Type`; a refusal leaves the store unchanged."""
    document = _document(await request.body(), request.headers.get("content-type"))
    return await run_in_threadpool(_apply, document, identity, services, dry_run=dry_run)


@router.get("/export", response_model=ConfigDocument, dependencies=[_VIEW])
def export_config(services: Services) -> ConfigDocument:
    """Every project with its standing links, every active, non-built-in work source and repository, and every
    active scope and routine (its linked scope set included) named in its project by slug, with every field, and
    every active secret name, as a document that applies back as a no-op. Retired records are left out, since
    applying one would enable it."""
    retired = services.secret_catalog.retired_names()
    projects = services.projects.list_all()
    slug_of: dict[str, str] = {project.project_id: project.slug for project in projects}
    live_routines = [
        r for r in sorted(services.routines.list_all(), key=lambda r: (slug_of[r.project_id], r.name)) if not r.retired
    ]
    scope_links = services.routine_scopes.list_scopes_for([r.routine_id for r in live_routines])
    sources = services.work_source_records.list_all(include_retired=False)
    repositories = services.repository_records.list_all(include_retired=False)
    forges = services.forge_records.get_many(
        list({r.fields.forge_id for r in sources} | {r.fields.forge_id for r in repositories})
    )
    repository_slugs = {r.repository_id: r.slug for r in services.repository_records.list_all(include_retired=True)}
    source_slugs = {r.work_source_id: r.slug for r in services.work_source_records.list_all(include_retired=True)}
    project_ids = [project.project_id for project in projects]
    source_links = services.project_links.sources_of_projects(project_ids)
    repository_links = services.project_links.repositories_of_projects(project_ids)
    return ConfigDocument(
        version=1,
        projects=[
            ProjectDocument(
                slug=project.slug,
                name=project.name,
                description=project.description,
                color=project.color,
                sources=sorted(source_slugs[link.work_source_id] for link in source_links.get(project.project_id, [])),
                repositories=sorted(
                    repository_slugs[link.repository_id] for link in repository_links.get(project.project_id, [])
                ),
            )
            for project in projects
        ],
        secrets=[s.name for s in services.secret_catalog.list_all() if s.name not in retired],
        work_sources=[
            WorkSourceDocument(
                slug=r.slug,
                name=r.name,
                provider=r.fields.provider,
                locator=r.fields.locator,
                api_base=forges[r.fields.forge_id].fields.api_base,
                web_base=forges[r.fields.forge_id].fields.web_base,
                annotate=r.fields.annotate,
                secret=r.fields.secret,
            )
            for r in sources
        ],
        repositories=[
            RepositoryDocument(
                slug=r.slug,
                name=r.name,
                forge_api_url=forges[r.fields.forge_id].fields.api_base,
                owner=r.fields.owner,
                repo=r.fields.repo,
                base_branch=r.fields.base_branch,
                secret_name=r.fields.secret_name,
            )
            for r in repositories
        ],
        scopes=[
            ScopeEntry(slug=s.slug, description=s.description, project=slug_of[s.project_id])
            for s in sorted(services.scopes.list_all(), key=lambda s: (slug_of[s.project_id], s.slug))
            if not s.retired
        ],
        routines=[
            RoutineEntry(
                name=r.name,
                graph_name=r.graph_name,
                default_scope_slug=r.default_scope_slug,
                default_model=r.default_model,
                default_effort=r.default_effort,
                default_harnesses=r.default_harnesses,
                scopes=scope_links[r.routine_id],
                project=slug_of[r.project_id],
            )
            for r in live_routines
        ],
    )
