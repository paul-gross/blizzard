"""Configuration routes — the served document schemas, the change log, and the declarative
document: ``POST /apply`` reconciles one, ``GET /export`` writes the current one.

The schemas, the change log and the export are read-only (``Permission.FLEET_VIEW``); an apply needs
``Permission.CONFIG_EDIT`` and writes through ``ConfigAuthoring`` like every other configured-record verb,
always as ``Door.APPLY`` — the door is never read from the request (``bzh:config-apply``)."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Query, Request, status
from pydantic import ValidationError
from starlette.concurrency import run_in_threadpool

from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require, tenant_bound
from blizzard.hub.api.deps import Services
from blizzard.hub.api.project_params import PROJECT_REQUIRED, QueryProject
from blizzard.hub.api.user_names import user_names
from blizzard.hub.auth.models import ResolvedIdentity
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.composition import TenantServices
from blizzard.hub.documents import ConfigDecodeError, codec_for_media_type
from blizzard.hub.domain.config.apply import ApplyEntryRefused, EntryOutcome
from blizzard.hub.domain.config.changes import ConfigChange, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import ForgeChoice
from blizzard.hub.domain.config.forges import ForgeEdit, ForgeFields, ForgeId
from blizzard.hub.domain.config.landing_policies import DEFAULT_LANDING_POLICY, POLICY_CAPABILITIES, landing_sort_key
from blizzard.hub.domain.config.repositories import RepositoryDraft, RepositoryEdit, RepositoryId
from blizzard.hub.domain.config.work_sources import WorkSourceDraft, WorkSourceEdit, WorkSourceId
from blizzard.hub.domain.config_apply.entries import (
    DeclaredForge,
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
from blizzard.hub.domain.projects.links import RepositoryLink
from blizzard.hub.domain.projects.model import ProjectEdit, ProjectId
from blizzard.wire.config import (
    ConfigApplyOutcome,
    ConfigApplyResponse,
    ConfigChangesPage,
    ConfigChangeView,
    ConfigDocumentV2,
    FieldChangeView,
    ForgeEntry,
    ProjectDocument,
    RepositoryEntry,
    RoutineEntry,
    ScopeEntry,
    WorkSourceEntry,
)
from blizzard.wire.routine import RoutineDocument
from blizzard.wire.scope import ScopeDocument

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

#: The one document version a hub applies, exports and serves schemas for.
DOCUMENT_VERSION = 2
#: The code a document version other than :data:`DOCUMENT_VERSION` is refused under.
VERSION_RETIRED = "config_version_retired"

_SCHEMAS = {
    "forges": ForgeEntry,
    "work-sources": WorkSourceEntry,
    "repositories": RepositoryEntry,
    "scopes": ScopeDocument,
    "routines": RoutineDocument,
}


#: The kinds whose ``record_key`` is a repository's id, those whose is a work source's, and the kinds whose is a
#: forge's and a project's.
_REPOSITORY_KEYED = (RecordKind.REPOSITORY, RecordKind.PROJECT_REPOSITORY_LINK)
_WORK_SOURCE_KEYED = (RecordKind.WORK_SOURCE, RecordKind.PROJECT_SOURCE_LINK)
_FORGE_KEYED = (RecordKind.FORGE,)


def _version_retired(loc: list[str], version: object) -> HTTPException:
    """422 on ``loc`` for a document version this hub no longer reads."""
    if version is None:
        msg = f"a document must name version {DOCUMENT_VERSION}"
    else:
        stated = version if isinstance(version, int) else repr(version)
        msg = f"version {stated} is retired; send a version {DOCUMENT_VERSION} document"
    return HTTPException(
        status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
        detail=[
            {
                "loc": loc,
                "msg": msg,
                "type": VERSION_RETIRED,
            }
        ],
    )


def _labels(
    changes: list[ConfigChange], services: TenantServices
) -> tuple[dict[tuple[RecordKind, str], str], dict[str, str]]:
    """The current slug of each slugged record a page of changes names: one read each of the repositories, the work
    sources, the forges and the projects it keys (``bzh:bulk-reconstitution``)."""
    repository_ids = list({RepositoryId(c.record_key) for c in changes if c.record_kind in _REPOSITORY_KEYED})
    source_ids = list({WorkSourceId(c.record_key) for c in changes if c.record_kind in _WORK_SOURCE_KEYED})
    forge_ids = list(
        {ForgeId(c.record_key) for c in changes if c.record_kind in _FORGE_KEYED}
        | {
            ForgeId(value)
            for c in changes
            for d in c.diff
            if d.field == "forge"
            for value in (d.old, d.new)
            if isinstance(value, str)
        }
    )
    project_ids = list({ProjectId(c.record_key) for c in changes if c.record_kind is RecordKind.PROJECT})
    labels: dict[tuple[RecordKind, str], str] = {}
    for repository_id, repository in services.repository_records.get_many(repository_ids).items():
        labels.update({(kind, repository_id): repository.slug for kind in _REPOSITORY_KEYED})
    for source_id, source in services.work_source_records.get_many(source_ids).items():
        labels.update({(kind, source_id): source.slug for kind in _WORK_SOURCE_KEYED})
    forges = services.forge_records.get_many(forge_ids)
    forge_labels = {str(forge_id): f"{forge.slug} · {forge.name}" for forge_id, forge in forges.items()}
    for forge_id, forge in forges.items():
        labels.update({(kind, str(forge_id)): forge.slug for kind in _FORGE_KEYED})
    for project_id, project in services.projects.get_many(project_ids).items():
        labels[(RecordKind.PROJECT, project_id)] = project.slug
    return labels, forge_labels


def _actor_names(changes: list[ConfigChange], services: TenantServices) -> dict[str, str]:
    """The display name — else the username — of each user actor a page of changes names, in one read
    (``bzh:bulk-reconstitution``); an actor that is no user is absent."""
    return user_names(services, (c.actor for c in changes))


def _view(
    change: ConfigChange,
    labels: dict[tuple[RecordKind, str], str],
    names: dict[str, str],
    forge_labels: dict[str, str],
) -> ConfigChangeView:
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
        diff=[
            FieldChangeView(
                field=d.field,
                old=d.old,
                new=d.new,
                old_label=forge_labels.get(d.old) if d.field == "forge" and isinstance(d.old, str) else None,
                new_label=forge_labels.get(d.new) if d.field == "forge" and isinstance(d.new, str) else None,
            )
            for d in change.diff
        ],
        apply_id=change.apply_id,
        project_id=change.project_id,
    )


#: The schema extension listing each landing policy with the forge capabilities it requires and the ones it uses.
LANDING_POLICIES_KEY = "x-blizzard-landing-policies"


@router.get("/schema/{kind}", dependencies=[_VIEW])
def get_schema(kind: str, version: Annotated[int, Query(ge=1, le=2)] = DOCUMENT_VERSION) -> dict[str, object]:
    """The JSON Schema of a record kind's version-2 document entry; 404 for a kind with none yet, and 422
    `config_version_retired` for `version=1`. The `repositories` schema lists, under `x-blizzard-landing-policies`,
    every landing policy with the capabilities its forge must offer (`requires`) and the ones it uses when offered
    (`uses`)."""
    if version != DOCUMENT_VERSION:
        raise _version_retired(["query", "version"], version)
    model = _SCHEMAS.get(kind)
    if model is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no document schema for {kind!r}")
    schema = model.model_json_schema()
    if kind == "repositories":
        schema[LANDING_POLICIES_KEY] = [
            {"policy": policy.value, "requires": sorted(needs.requires), "uses": sorted(needs.uses)}
            for policy, needs in POLICY_CAPABILITIES.items()
        ]
    return schema


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
    labels, forge_labels = _labels(page, services)
    names = _actor_names(page, services)
    return ConfigChangesPage(
        changes=[_view(c, labels, names, forge_labels) for c in page],
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


def _entries(document: ConfigDocumentV2) -> DocumentEntries:
    """The domain's entries from a validated document: each entry's create fields, a sparse edit of only the
    fields the entry states (``model_fields_set``), and the forge it names."""
    sources: list[DeclaredWorkSource] = []
    for entry in document.work_sources:
        stated = {name: getattr(entry, name) for name in entry.model_fields_set if name != "slug"}
        reference = stated.pop("forge", UNSET)
        sources.append(
            DeclaredWorkSource(
                slug=entry.slug,
                name=entry.name,
                draft=WorkSourceDraft(
                    provider=entry.provider, locator=entry.locator, annotate=entry.annotate, secret=entry.secret
                ),
                edit=WorkSourceEdit(**stated),
                forge=ForgeChoice(forge=reference),
            )
        )
    repositories: list[DeclaredRepository] = []
    for repo in document.repositories:
        stated = {name: getattr(repo, name) for name in repo.model_fields_set if name != "slug"}
        reference = stated.pop("forge", UNSET)
        policy = repo.landing_policy
        if "landing_policy" in stated:
            stated["landing_policy"] = policy
        repositories.append(
            DeclaredRepository(
                slug=repo.slug,
                name=repo.name,
                draft=RepositoryDraft(
                    owner=repo.owner,
                    repo=repo.repo,
                    base_branch=repo.base_branch,
                    secret_name=repo.secret_name,
                    landing_policy=policy or DEFAULT_LANDING_POLICY,
                    park_timeout_hours=repo.park_timeout_hours,
                ),
                edit=RepositoryEdit(**stated),
                forge=ForgeChoice(forge=reference),
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
    routines: list[DeclaredRoutine] = []
    for routine in document.routines:
        # A name-only entry is the older form keyed by the name as the handle: it never touches the display name.
        not_edits: set[str] = {"scopes", "project"}
        if "slug" not in routine.model_fields_set:
            not_edits.add("name")
        stated = {name: getattr(routine, name) for name in routine.model_fields_set if name not in not_edits}
        routines.append(
            DeclaredRoutine(
                slug=routine.slug,
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
        forges=tuple(
            DeclaredForge(
                slug=f.slug,
                name=f.name,
                kind=f.kind,
                fields=ForgeFields(f.api_base, f.web_base),
                edit=ForgeEdit(**{name: getattr(f, name) for name in f.model_fields_set if name != "kind"}),
            )
            for f in document.forges
        ),
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
        landing_order=tuple(entry.landing_order) if entry.landing_order is not None else None,
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
            detail=[{"loc": loc, "msg": cause.message, "type": cause.code}],
        )
    return HTTPException(status_code=status.HTTP_409_CONFLICT, detail=f"{exc.section}[{exc.index}]: {cause}")


def _document(body: bytes, content_type: str | None) -> ConfigDocumentV2:
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
    version = mapping.get("version")
    if version != DOCUMENT_VERSION:
        raise _version_retired(["body", "version"], version)
    try:
        return ConfigDocumentV2.model_validate(mapping)
    except ValidationError as exc:
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_CONTENT,
            detail=[{"loc": ["body", *e["loc"]], "msg": e["msg"], "type": e["type"]} for e in exc.errors()],
        ) from exc


def _apply(
    document: ConfigDocumentV2,
    identity: ResolvedIdentity,
    services: TenantServices,
    *,
    dry_run: bool,
) -> ConfigApplyResponse:
    try:
        applied = services.config_application.apply(_entries(document), actor=identity.user_id, dry_run=dry_run)
    except ApplyEntryRefused as exc:
        raise _refusal(exc) from exc
    return ConfigApplyResponse(
        dry_run=dry_run,
        apply_id=applied.apply_id,
        outcomes=[_outcome(o) for o in applied.outcomes],
    )


_APPLY_BODY = {
    "required": True,
    "content": {
        media_type: {
            "schema": {
                "oneOf": [{"$ref": "#/components/schemas/ConfigDocumentV2"}],
                "discriminator": {
                    "propertyName": "version",
                    "mapping": {"2": "#/components/schemas/ConfigDocumentV2"},
                },
            }
        }
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
    """Reconcile a version-2 document (YAML or JSON, by `Content-Type`) in one transaction: create what is absent,
    edit only the fields an entry states, enable what is retired, leave unnamed records alone. Forges are declared,
    and every work source and repository names its forge by id or slug. `dry_run` rolls the same transaction back,
    with the same `outcomes` and no `apply_id`. 422 invalid — `config_version_retired` for any `version` but 2, a
    missing one included — 409 conflict, 415 unknown `Content-Type`; a refusal leaves the store unchanged."""
    document = _document(await request.body(), request.headers.get("content-type"))
    return await run_in_threadpool(_apply, document, identity, services, dry_run=dry_run)


@router.get("/export", response_model=ConfigDocumentV2, dependencies=[_VIEW])
def export_config(
    services: Services, version: Annotated[int, Query(ge=1, le=2)] = DOCUMENT_VERSION
) -> ConfigDocumentV2:
    """Every project with its standing links, every active forge, every active, non-built-in work source and
    repository, and every active scope and routine (its linked scope set included) named in its project by slug, with
    every field, and every active secret name, as a version-2 document that applies back as a no-op. Retired records
    are left out, since applying one would enable it. 422 `config_version_retired` for `version=1`."""
    if version != DOCUMENT_VERSION:
        raise _version_retired(["query", "version"], version)
    retired = services.secret_catalog.retired_names()
    projects = services.projects.list_all()
    slug_of: dict[str, str] = {project.project_id: project.slug for project in projects}
    live_routines = [
        r for r in sorted(services.routines.list_all(), key=lambda r: (slug_of[r.project_id], r.slug)) if not r.retired
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
    return ConfigDocumentV2(
        version=DOCUMENT_VERSION,
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
                landing_order=_landing_order(repository_links.get(project.project_id, []), repository_slugs),
            )
            for project in projects
        ],
        secrets=[s.name for s in services.secret_catalog.list_all() if s.name not in retired],
        scopes=[
            ScopeEntry(slug=s.slug, description=s.description, project=slug_of[s.project_id])
            for s in sorted(services.scopes.list_all(), key=lambda s: (slug_of[s.project_id], s.slug))
            if not s.retired
        ],
        routines=[
            RoutineEntry(
                slug=r.slug,
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
        forges=[
            ForgeEntry(slug=f.slug, name=f.name, kind=f.kind, api_base=f.fields.api_base, web_base=f.fields.web_base)
            for f in services.forge_records.list_all(include_retired=False)
        ],
        work_sources=[
            WorkSourceEntry(
                slug=r.slug,
                name=r.name,
                provider=r.fields.provider,
                locator=r.fields.locator,
                forge=forges[r.fields.forge_id].slug,
                annotate=r.fields.annotate,
                secret=r.fields.secret,
            )
            for r in sources
        ],
        repositories=[
            RepositoryEntry(
                slug=r.slug,
                name=r.name,
                forge=forges[r.fields.forge_id].slug,
                owner=r.fields.owner,
                repo=r.fields.repo,
                base_branch=r.fields.base_branch,
                secret_name=r.fields.secret_name,
                landing_policy=r.fields.landing_policy,
                park_timeout_hours=r.fields.park_timeout_hours,
            )
            for r in repositories
        ],
    )


def _landing_order(links: list[RepositoryLink], slugs: Mapping[RepositoryId, str]) -> list[str] | None:
    """The slugs of the repositories ``links`` rank, in landing order; ``None`` when they rank none."""
    ranked = sorted(
        (link for link in links if link.position is not None),
        key=lambda link: landing_sort_key(link.position, slugs[link.repository_id]),
    )
    return [slugs[link.repository_id] for link in ranked] or None
