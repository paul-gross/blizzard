"""Configuration routes — the served document schemas, the change log, and the declarative
document: ``POST /apply`` reconciles one, ``GET /export`` writes the current one.

The schemas, the change log and the export are read-only (``Permission.FLEET_VIEW``); an apply needs
``Permission.CONFIG_EDIT`` and writes through ``ConfigAuthoring`` like every other configured-record verb,
always as ``Door.APPLY`` — the door is never read from the request (``bzh:config-apply``)."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from blizzard.auth_core import Permission
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import dto
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.project_params import PROJECT_REQUIRED, QueryProject
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.composition import TenantServices
from blizzard.hub.documents.codec import ConfigDecodeError, codec_for_media_type
from blizzard.hub.domain.config.apply import (
    ROUTINES_SECTION,
    SCOPES_SECTION,
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
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryId,
    RepositoryRef,
    RepositorySlug,
)
from blizzard.hub.domain.config.work_sources import (
    ConfigFieldError,
    ConfiguredWorkSource,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceId,
    WorkSourceRef,
    WorkSourceSlug,
)
from blizzard.hub.domain.garden.declarations import RoutineDeclaration, ScopeDeclaration
from blizzard.hub.domain.garden.routines import Routine, RoutineEdit
from blizzard.hub.domain.garden.scopes import ScopeEdit
from blizzard.hub.domain.projects.declarations import (
    EntryProjectRequired,
    PlannedRepositories,
    PlannedWorkSources,
    ProjectDeclaration,
    ProjectsPlan,
    StoredProjects,
    plan_projects,
)
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import Project, ProjectEdit, ProjectId, ProjectSlug
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


#: The kinds whose ``record_key`` is a repository's id, those whose is a work source's, and the kind whose is a
#: project's.
_REPOSITORY_KEYED = (RecordKind.REPOSITORY, RecordKind.PROJECT_REPOSITORY_LINK)
_WORK_SOURCE_KEYED = (RecordKind.WORK_SOURCE, RecordKind.PROJECT_SOURCE_LINK)


def _labels(changes: list[ConfigChange], services: TenantServices) -> dict[tuple[RecordKind, str], str]:
    """The current slug of each slugged record a page of changes names: one read each of the repositories, the work
    sources and the projects it keys (``bzh:bulk-reconstitution``)."""
    repository_ids = list({RepositoryId(c.record_key) for c in changes if c.record_kind in _REPOSITORY_KEYED})
    source_ids = list({WorkSourceId(c.record_key) for c in changes if c.record_kind in _WORK_SOURCE_KEYED})
    project_ids = list({ProjectId(c.record_key) for c in changes if c.record_kind is RecordKind.PROJECT})
    labels: dict[tuple[RecordKind, str], str] = {}
    for repository_id, repository in services.repository_records.get_many(repository_ids).items():
        labels.update({(kind, repository_id): repository.slug for kind in _REPOSITORY_KEYED})
    for source_id, source in services.work_source_records.get_many(source_ids).items():
        labels.update({(kind, source_id): source.slug for kind in _WORK_SOURCE_KEYED})
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
    """The stored key a change-log filter names: the id of the project, repository or work source an id or slug
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
    return key


def _declaration(
    document: ConfigDocument, projects: ProjectsPlan, held: _Repositories, sources_held: _WorkSources
) -> ConfigDeclaration:
    """The domain's declarations from a validated document: each entry's create fields, and a sparse edit
    of only the fields the entry states (``model_fields_set``); the planned ``projects`` section, and each
    scope and routine within the project it names, as the apply leaves the projects."""
    sources = []
    for entry in document.work_sources:
        stated = {name: getattr(entry, name) for name in entry.model_fields_set if name != "slug"}
        sources.append(
            WorkSourceDeclaration(
                slug=entry.slug,
                name=entry.name,
                work_source_id=sources_held.ids[entry.slug],
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
        stated = {name: getattr(repo, name) for name in repo.model_fields_set if name != "slug"}
        repositories.append(
            RepositoryDeclaration(
                slug=repo.slug,
                name=repo.name,
                repository_id=held.ids[repo.slug],
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
    scopes, routines = _garden_declarations(document, projects)
    return ConfigDeclaration(
        secrets=tuple(document.secrets),
        work_sources=tuple(sources),
        repositories=tuple(repositories),
        projects=projects.section,
        scopes=scopes,
        routines=routines,
    )


def _garden_declarations(
    document: ConfigDocument, projects: ProjectsPlan
) -> tuple[tuple[ScopeDeclaration, ...], tuple[RoutineDeclaration, ...]]:
    """The document's scope and routine declarations, each within the project its entry names — the tenant's
    only one, as the apply leaves it, for an entry naming none."""
    scopes = [
        ScopeDeclaration(
            name=scope.slug,
            description=scope.description,
            edit=ScopeEdit(
                **{name: getattr(scope, name) for name in scope.model_fields_set if name not in ("slug", "project")}
            ),
            project_id=projects.project_of(SCOPES_SECTION, index, scope.project).project_id,
        )
        for index, scope in enumerate(document.scopes)
    ]
    routines = []
    for index, routine in enumerate(document.routines):
        project_id = projects.project_of(ROUTINES_SECTION, index, routine.project).project_id
        stated = {
            name: getattr(routine, name) for name in routine.model_fields_set if name not in ("scopes", "project")
        }
        routines.append(
            RoutineDeclaration(
                name=routine.name,
                graph_name=routine.graph_name,
                default_scope_slug=routine.default_scope_slug,
                default_model=routine.default_model,
                default_effort=routine.default_effort,
                default_harnesses=routine.default_harnesses,
                edit=RoutineEdit(**stated),
                project_id=project_id,
                scopes=routine.scopes if "scopes" in routine.model_fields_set else None,
            )
        )
    return tuple(scopes), tuple(routines)


def _stored(
    declaration: ConfigDeclaration, services: TenantServices, held: _Repositories, sources_held: _WorkSources
) -> StoredConfig:
    secrets = services.secret_catalog.get_many(list(declaration.secrets))
    declared_slugs = {d.slug for d in declaration.repositories if isinstance(d, RepositoryDeclaration)}
    source_slugs = {d.slug for d in declaration.work_sources if isinstance(d, WorkSourceDeclaration)}
    retired = services.secret_catalog.retired_names(list(secrets))
    scopes: dict[tuple[str, str], DeclarableRecord] = {
        (scope.project_id, scope.slug): scope for scope in services.scopes.list_all()
    }
    graph_names: set[str] = set()
    names: dict[str, list[str]] = {}
    for entry in declaration.routines:
        assert isinstance(entry, RoutineDeclaration)
        graph_names.add(entry.graph_name)
        names.setdefault(entry.project_id, []).append(entry.name)
    routines: dict[tuple[str, str], DeclarableRecord] = {
        (project_id, name): routine
        for project_id, project_names in names.items()
        for name, routine in services.routines.get_many_by_name(project_id, project_names).items()
    }
    ids = {key: r.routine_id for key, r in routines.items() if isinstance(r, Routine)}
    scope_links = services.routine_scopes.list_scopes_for(list(ids.values()))
    linked = {key: tuple(scope_links[routine_id]) for key, routine_id in ids.items()}
    declared_scopes = [d for d in declaration.scopes if isinstance(d, ScopeDeclaration)]
    return StoredConfig(
        work_sources={slug: sources_held.stored[slug] for slug in source_slugs if slug in sources_held.stored},
        former_work_sources={slug: sources_held.former[slug] for slug in source_slugs if slug in sources_held.former},
        repositories={slug: held.stored[slug] for slug in declared_slugs if slug in held.stored},
        former_repositories={slug: held.former[slug] for slug in declared_slugs if slug in held.former},
        secrets={name: RecordState.of(name in retired) for name in secrets},
        scopes={d.key: scopes[d.key] for d in declared_scopes if d.key in scopes},
        routines=routines,
        references=DeclaredReferences(
            scopes=frozenset(scopes) | {d.key for d in declared_scopes},
            enabled_graphs=frozenset(services.graphs.enabled_names(sorted(graph_names))),
            linked_scopes=linked,
        ),
    )


@dto
@dataclass(frozen=True)
class _Repositories:
    """The repositories a document's apply meets: every stored one by current slug (retired included); the one that
    left each slug the document declares or a project lists that none holds; and the id each declared slug takes —
    the stored record's, or one minted for a record the document creates, so the projects section can link to it."""

    stored: dict[str, ConfiguredRepository]
    former: dict[str, ConfiguredRepository]
    ids: dict[str, RepositoryId] = field(default_factory=dict)

    @classmethod
    def of(cls, document: ConfigDocument, services: TenantServices) -> _Repositories:
        stored = {r.slug: r for r in services.repository_records.list_all(include_retired=True)}
        named = {entry.slug for entry in document.repositories} | {
            reference for entry in document.projects for reference in entry.repositories
        }
        former: dict[str, ConfiguredRepository] = {}
        for value in sorted(named - stored.keys()):
            slug = RepositorySlug.of(value)
            left = services.repository_records.get_by_former_slug(slug) if slug is not None else None
            if left is not None:
                former[value] = left
        ids = {
            entry.slug: stored[entry.slug].repository_id
            if entry.slug in stored
            else RepositoryId(Id.mint(IdPrefix.REPOSITORY, services.clock).value)
            for entry in document.repositories
        }
        return cls(stored, former, ids)

    def planned(self) -> PlannedRepositories:
        """The repositories as the apply leaves them: those stored, and those the document creates."""
        created = tuple(
            RepositoryRef(repository_id, slug) for slug, repository_id in self.ids.items() if slug not in self.stored
        )
        return PlannedRepositories(
            (*(r.ref() for r in self.stored.values()), *created), {slug: r.ref() for slug, r in self.former.items()}
        )


@dto
@dataclass(frozen=True)
class _WorkSources:
    """The work sources a document's apply meets, as :class:`_Repositories` holds the repositories: every stored one by
    current slug (retired included); the one that left each slug the document declares or a project lists that none
    holds; and the id each declared slug takes."""

    stored: dict[str, ConfiguredWorkSource]
    former: dict[str, ConfiguredWorkSource]
    ids: dict[str, WorkSourceId] = field(default_factory=dict)

    @classmethod
    def of(cls, document: ConfigDocument, services: TenantServices) -> _WorkSources:
        stored = {r.slug: r for r in services.work_source_records.list_all(include_retired=True)}
        named = {entry.slug for entry in document.work_sources} | {
            reference for entry in document.projects for reference in entry.sources
        }
        former: dict[str, ConfiguredWorkSource] = {}
        for value in sorted(named - stored.keys()):
            slug = WorkSourceSlug.of(value)
            left = services.work_source_records.get_by_former_slug(slug) if slug is not None else None
            if left is not None:
                former[value] = left
        ids = {
            entry.slug: stored[entry.slug].work_source_id
            if entry.slug in stored
            else WorkSourceId(Id.mint(IdPrefix.WORK_SOURCE, services.clock).value)
            for entry in document.work_sources
        }
        return cls(stored, former, ids)

    def planned(self) -> tuple[tuple[WorkSourceRef, ...], dict[str, WorkSourceRef]]:
        """The work sources as the apply leaves them — those stored, and those the document creates — and the one that
        left each referenced slug."""
        created = tuple(
            WorkSourceRef(work_source_id, slug) for slug, work_source_id in self.ids.items() if slug not in self.stored
        )
        return (*(r.ref() for r in self.stored.values()), *created), {s: r.ref() for s, r in self.former.items()}


def _projects(
    document: ConfigDocument,
    services: TenantServices,
    ctx: ChangeContext,
    held_repositories: _Repositories,
    held_sources: _WorkSources,
) -> ProjectsPlan:
    """The planned ``projects`` section, from what the store holds for the projects and links the document names."""
    projects = services.projects.list_all()
    current = {project.slug: project for project in projects}
    named = {entry.slug for entry in document.projects} | {
        entry.project for entry in (*document.scopes, *document.routines) if entry.project is not None
    }
    former: dict[str, Project] = {}
    for value in sorted(named - current.keys()):
        slug = ProjectSlug.of(value)
        left = services.projects.get_by_former_slug(slug) if slug is not None else None
        if left is not None:
            former[value] = left
    links = services.project_links
    source_links: dict[tuple[str, str], SourceLink] = {}
    repository_links: dict[tuple[str, str], RepositoryLink] = {}
    planned = held_repositories.planned()
    planned_sources, former_sources = held_sources.planned()
    source_lookup = PlannedWorkSources(planned_sources, former_sources)
    # Every standing link may be unlinked, and a listed one in any state relinked: the keys of both are gathered
    # across the projects first, then read once per seam.
    source_projects: list[ProjectId] = []
    repository_projects: list[ProjectId] = []
    listed_sources: list[tuple[ProjectId, WorkSourceId]] = []
    listed_repositories: list[tuple[ProjectId, RepositoryId]] = []
    for entry in document.projects:
        project = current.get(entry.slug)
        if project is None:
            continue
        held = project.project_id
        if "sources" in entry.model_fields_set:
            source_projects.append(held)
            listed_sources.extend(
                (held, ref.work_source_id)
                for reference in entry.sources
                if (ref := source_lookup.resolve(reference)) is not None
            )
        if "repositories" in entry.model_fields_set:
            repository_projects.append(held)
            listed_repositories.extend(
                (held, ref.repository_id)
                for reference in entry.repositories
                if (ref := planned.resolve(reference)) is not None
            )
    for standing in links.sources_of_projects(source_projects).values():
        source_links.update({(link.project_id, link.work_source_id): link for link in standing})
    listed = links.source_links_for(listed_sources).values()
    source_links.update({(link.project_id, link.work_source_id): link for link in listed})
    for standing_repositories in links.repositories_of_projects(repository_projects).values():
        repository_links.update({(link.project_id, link.repository_id): link for link in standing_repositories})
    named = links.repository_links_for(listed_repositories).values()
    repository_links.update({(link.project_id, link.repository_id): link for link in named})
    stored = StoredProjects(
        projects=tuple(projects),
        former=former,
        source_links=source_links,
        repository_links=repository_links,
        sources=planned_sources,
        former_sources=former_sources,
        repositories=planned.repositories,
        former_repositories=dict(planned.former),
    )
    declared = [_project_declaration(entry) for entry in document.projects]
    return plan_projects(declared, stored, ctx, at=services.clock.now())


def _project_declaration(entry: ProjectDocument) -> ProjectDeclaration:
    stated = entry.model_fields_set
    return ProjectDeclaration(
        slug=entry.slug,
        edit=ProjectEdit(
            **({"name": entry.name} if "name" in stated and entry.name is not None else {}),
            **({"description": entry.description} if "description" in stated else {}),
        ),
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
    apply_id = None if dry_run else Id.mint(IdPrefix.CONFIG_APPLY, services.clock).value
    ctx = ChangeContext(actor=identity.user_id, door=Door.APPLY, apply_id=apply_id)
    try:
        held = _Repositories.of(document, services)
        sources = _WorkSources.of(document, services)
        projects = _projects(document, services, ctx, held, sources)
        declaration = _declaration(document, projects, held, sources)
        outcomes = services.config_authoring.apply(
            declaration, _stored(declaration, services, held, sources), ctx, dry_run=dry_run
        )
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
                sources=sorted(source_slugs[link.work_source_id] for link in source_links.get(project.project_id, [])),
                repositories=sorted(
                    repository_slugs[link.repository_id] for link in repository_links.get(project.project_id, [])
                ),
            )
            for project in projects
        ],
        secrets=[s.name for s in services.secret_catalog.list_all() if s.name not in retired],
        work_sources=[WorkSourceDocument(slug=r.slug, name=r.name, **vars(r.fields)) for r in sources],
        repositories=[RepositoryDocument(slug=r.slug, name=r.name, **vars(r.fields)) for r in repositories],
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
