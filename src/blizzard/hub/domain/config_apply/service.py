"""``ConfigApplication`` — the use case behind a declarative apply: it loads what the entries name, mints the ids and
the instant they write under, plans the ``projects`` section, and hands the whole declaration to
:class:`~blizzard.hub.domain.config.authoring.ConfigAuthoring` to reconcile (``bzh:config-apply``)."""

from __future__ import annotations

from dataclasses import dataclass, field, replace

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config.apply import (
    FORGES_SECTION,
    REPOSITORIES_SECTION,
    ROUTINES_SECTION,
    SCOPES_SECTION,
    WORK_SOURCES_SECTION,
    ApplyEntryRefused,
    ConfigDeclaration,
    DeclarableRecord,
    DeclaredReferences,
    EntryOutcome,
    ForgeDeclaration,
    RepositoryDeclaration,
    StoredConfig,
    WorkSourceDeclaration,
    require_declarable_forge,
)
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, Door, RecordState
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import (
    REPOSITORY_FORGE_RULES,
    WORK_SOURCE_FORGE_RULES,
    ForgePlanner,
    OldFieldUsed,
)
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeId,
    ForgeSlug,
    IReadForgeRepository,
    PlannedForges,
)
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IReadRepositoryRecordRepository,
    RepositoryId,
    RepositoryRef,
    RepositorySlug,
)
from blizzard.hub.domain.config.secrets import ISecretCatalog
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    IReadWorkSourceRepository,
    WorkSourceId,
    WorkSourceRef,
    WorkSourceSlug,
    require_known_provider,
)
from blizzard.hub.domain.config_apply.entries import DocumentEntries
from blizzard.hub.domain.garden.declarations import RoutineDeclaration, ScopeDeclaration
from blizzard.hub.domain.garden.routines import IReadRoutineRepository, IReadRoutineScopeRepository, Routine
from blizzard.hub.domain.garden.scopes import IReadScopeRepository
from blizzard.hub.domain.graph.model import IReadGraphRepository
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType
from blizzard.hub.domain.projects.color import ColorPick
from blizzard.hub.domain.projects.declarations import (
    PlannedRepositories,
    PlannedWorkSources,
    ProjectsPlan,
    StoredProjects,
    plan_projects,
)
from blizzard.hub.domain.projects.links import IReadProjectLinks, RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import IReadProjectRepository, Project, ProjectId, ProjectSlug


@domain_model
@dataclass(frozen=True)
class ApplyResult:
    """What an apply decided: the id its changes share — ``None`` on a dry run, which commits nothing — an outcome row
    per named record, and a warning per old address field an entry used."""

    apply_id: str | None
    outcomes: tuple[EntryOutcome, ...]
    warnings: tuple[OldFieldUsed, ...] = ()


@domain_model
@dataclass(frozen=True)
class _Repositories:
    """The repositories a document's apply meets: every stored one by current slug (retired included); the one that
    left each slug the document declares or a project lists that none holds; and the id each declared slug takes —
    the stored record's, or one minted for a record the document creates, so the projects section can link to it."""

    stored: dict[str, ConfiguredRepository]
    former: dict[str, ConfiguredRepository]
    ids: dict[str, RepositoryId] = field(default_factory=dict[str, RepositoryId])

    def planned(self) -> PlannedRepositories:
        """The repositories as the apply leaves them: those stored, and those the document creates."""
        created = tuple(
            RepositoryRef(repository_id, slug) for slug, repository_id in self.ids.items() if slug not in self.stored
        )
        return PlannedRepositories(
            (*(r.ref() for r in self.stored.values()), *created), {slug: r.ref() for slug, r in self.former.items()}
        )


@domain_model
@dataclass(frozen=True)
class _WorkSources:
    """The work sources a document's apply meets, as :class:`_Repositories` holds the repositories: every stored one by
    current slug (retired included); the one that left each slug the document declares or a project lists that none
    holds; and the id each declared slug takes."""

    stored: dict[str, ConfiguredWorkSource]
    former: dict[str, ConfiguredWorkSource]
    ids: dict[str, WorkSourceId] = field(default_factory=dict[str, WorkSourceId])

    def planned(self) -> tuple[tuple[WorkSourceRef, ...], dict[str, WorkSourceRef]]:
        """The work sources as the apply leaves them — those stored, and those the document creates — and the one that
        left each referenced slug."""
        created = tuple(
            WorkSourceRef(work_source_id, slug) for slug, work_source_id in self.ids.items() if slug not in self.stored
        )
        return (*(r.ref() for r in self.stored.values()), *created), {s: r.ref() for s, r in self.former.items()}


class ConfigApplication:
    """Reconciles a document's entries against the tenant's stored records in one transaction, or rolls the same
    transaction back for a dry run."""

    def __init__(
        self,
        *,
        authoring: ConfigAuthoring,
        repositories: IReadRepositoryRecordRepository,
        work_sources: IReadWorkSourceRepository,
        forges: IReadForgeRepository,
        projects: IReadProjectRepository,
        links: IReadProjectLinks,
        scopes: IReadScopeRepository,
        routines: IReadRoutineRepository,
        routine_scopes: IReadRoutineScopeRepository,
        graphs: IReadGraphRepository,
        secrets: ISecretCatalog,
        pick: ColorPick,
        clock: IClock,
    ) -> None:
        self._authoring = authoring
        self._repositories = repositories
        self._work_sources = work_sources
        self._forges = forges
        self._projects = projects
        self._links = links
        self._scopes = scopes
        self._routines = routines
        self._routine_scopes = routine_scopes
        self._graphs = graphs
        self._secrets = secrets
        self._pick = pick
        self._clock = clock

    def apply(self, entries: DocumentEntries, *, actor: str, dry_run: bool) -> ApplyResult:
        """Apply ``entries`` as ``actor`` through the apply door. :class:`ApplyEntryRefused` names the entry that
        refused, and the store is left as it was."""
        apply_id = None if dry_run else Id.mint(IdPrefix.CONFIG_APPLY, self._clock).value
        ctx = ChangeContext(actor=actor, door=Door.APPLY, apply_id=apply_id)
        repositories = self._held_repositories(entries)
        sources = self._held_work_sources(entries)
        projects = self._plan_projects(entries, ctx, repositories, sources)
        held_forges = {f.slug: f for f in self._forges.list_all(include_retired=True)}
        declared_forges, lookup, former_forges = self._held_forges(entries, held_forges, ctx)
        planner = ForgePlanner(lookup.records, ctx, at=self._clock.now())
        declaration, warnings = self._declaration(entries, projects, repositories, sources, planner, lookup)
        declaration = replace(declaration, declared_forges=declared_forges)
        outcomes = self._authoring.apply(
            declaration,
            self._stored(declaration, repositories, sources, held_forges, former_forges),
            ctx,
            dry_run=dry_run,
        )
        if apply_id is not None:
            self._authoring.record_old_fields(warnings, record=f"config apply {apply_id}")
        return ApplyResult(apply_id, tuple(outcomes), warnings)

    def _held_forges(
        self, entries: DocumentEntries, held: dict[str, ConfiguredForge], ctx: ChangeContext
    ) -> tuple[tuple[ForgeDeclaration, ...], PlannedForges, dict[str, ConfiguredForge]]:
        former: dict[str, ConfiguredForge] = {}
        for entry in entries.forges:
            if entry.slug not in held and (slug := ForgeSlug.of(entry.slug)) is not None:
                left = self._forges.get_by_former_slug(slug)
                if left is not None:
                    former[entry.slug] = left
        projected = dict(held)
        declarations: list[ForgeDeclaration] = []
        seen: set[str] = set()
        for index, entry in enumerate(entries.forges):
            try:
                if entry.slug in seen:
                    raise ConfigFieldError("slug", f"{entry.slug!r} is named twice in this document")
                seen.add(entry.slug)
                stored = held.get(entry.slug)
                require_declarable_forge(entry.slug, entry.kind, stored, former.get(entry.slug))
                forge_id = stored.forge_id if stored else ForgeId(Id.mint(IdPrefix.FORGE, self._clock).value)
                if stored is None:
                    record, _ = ConfiguredForge.new(
                        entry.slug, entry.name, entry.kind, entry.fields, ctx, at=self._clock.now(), forge_id=forge_id
                    )
                else:
                    enabled = (
                        stored.set_retired(False, ctx, if_match=None, at=self._clock.now()) if stored.retired else None
                    )
                    record = stored if enabled is None else enabled[0]
                    edited = record.edit(entry.edit, ctx, if_match=None, at=self._clock.now())
                    record = record if edited is None else edited[0]
                projected[entry.slug] = record
                declarations.append(
                    ForgeDeclaration(entry.slug, entry.name, entry.kind, entry.fields, entry.edit, forge_id)
                )
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(FORGES_SECTION, index, exc) from exc
        origins: dict[str, str] = {}
        for forge in projected.values():
            if (holder := origins.get(forge.fields.api_base)) is not None and holder != forge.slug:
                index = next((i for i, e in enumerate(entries.forges) if e.slug == forge.slug), 0)
                raise ApplyEntryRefused(
                    FORGES_SECTION, index, ConfigFieldError("api_base", f"origin is held by forge {holder}")
                )
            origins[forge.fields.api_base] = forge.slug
        return tuple(declarations), PlannedForges(tuple(projected.values()), former), former

    def _held_repositories(self, entries: DocumentEntries) -> _Repositories:
        stored = {r.slug: r for r in self._repositories.list_all(include_retired=True)}
        named = {entry.slug for entry in entries.repositories} | {
            reference
            for entry in entries.projects
            for reference in (*(entry.repositories or ()), *(entry.landing_order or ()))
        }
        former: dict[str, ConfiguredRepository] = {}
        for value in sorted(named - stored.keys()):
            slug = RepositorySlug.of(value)
            left = self._repositories.get_by_former_slug(slug) if slug is not None else None
            if left is not None:
                former[value] = left
        ids = {
            entry.slug: stored[entry.slug].repository_id
            if entry.slug in stored
            else RepositoryId(Id.mint(IdPrefix.REPOSITORY, self._clock).value)
            for entry in entries.repositories
        }
        return _Repositories(stored, former, ids)

    def _held_work_sources(self, entries: DocumentEntries) -> _WorkSources:
        stored = {r.slug: r for r in self._work_sources.list_all(include_retired=True)}
        named = {entry.slug for entry in entries.work_sources} | {
            reference for entry in entries.projects for reference in entry.sources or ()
        }
        former: dict[str, ConfiguredWorkSource] = {}
        for value in sorted(named - stored.keys()):
            slug = WorkSourceSlug.of(value)
            left = self._work_sources.get_by_former_slug(slug) if slug is not None else None
            if left is not None:
                former[value] = left
        ids = {
            entry.slug: stored[entry.slug].work_source_id
            if entry.slug in stored
            else WorkSourceId(Id.mint(IdPrefix.WORK_SOURCE, self._clock).value)
            for entry in entries.work_sources
        }
        return _WorkSources(stored, former, ids)

    def _plan_projects(
        self,
        entries: DocumentEntries,
        ctx: ChangeContext,
        held_repositories: _Repositories,
        held_sources: _WorkSources,
    ) -> ProjectsPlan:
        """The planned ``projects`` section, from what the store holds for the projects and links the entries name."""
        projects = self._projects.list_all()
        current = {project.slug: project for project in projects}
        named = {entry.slug for entry in entries.projects} | {
            entry.project for entry in (*entries.scopes, *entries.routines) if entry.project is not None
        }
        former: dict[str, Project] = {}
        for value in sorted(named - current.keys()):
            slug = ProjectSlug.of(value)
            left = self._projects.get_by_former_slug(slug) if slug is not None else None
            if left is not None:
                former[value] = left
        links = self._links
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
        for entry in entries.projects:
            project = current.get(entry.slug)
            if project is None:
                continue
            held = project.project_id
            if entry.sources is not None:
                source_projects.append(held)
                listed_sources.extend(
                    (held, ref.work_source_id)
                    for reference in entry.sources
                    if (ref := source_lookup.resolve(reference)) is not None
                )
            if entry.repositories is None and entry.landing_order is not None:
                repository_projects.append(held)
            if entry.repositories is not None:
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
        named_links = links.repository_links_for(listed_repositories).values()
        repository_links.update({(link.project_id, link.repository_id): link for link in named_links})
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
        return plan_projects(list(entries.projects), stored, ctx, pick=self._pick, at=self._clock.now())

    @staticmethod
    def _declaration(
        entries: DocumentEntries,
        projects: ProjectsPlan,
        held: _Repositories,
        sources_held: _WorkSources,
        planner: ForgePlanner,
        forges: PlannedForges,
    ) -> tuple[ConfigDeclaration, tuple[OldFieldUsed, ...]]:
        """The domain's declarations from the entries: each record under the id it takes and on the forge it names —
        the forges no origin's forge holds yet planned beside them — and each scope and routine within the project it
        names, as the apply leaves the projects; with a warning per old address field an entry used. An entry's
        :class:`ConfigFieldError` on its forge is refused at the entry."""
        warnings: list[OldFieldUsed] = []
        for entry in entries.work_sources:
            planner.foresee(entry.forge, WORK_SOURCE_FORGE_RULES, creating=entry.slug not in sources_held.stored)
        work_sources: list[WorkSourceDeclaration] = []
        for index, entry in enumerate(entries.work_sources):
            stored = sources_held.stored.get(entry.slug)
            if not isinstance(entry.edit.provider, UnsetType):
                provider = entry.edit.provider
            else:
                provider = entry.draft.provider if stored is None else stored.fields.provider
            current = None if stored is None else stored.fields.forge_id
            try:
                require_known_provider(provider)
                resolved = planner.decide(
                    replace(entry.forge, named=forges.resolve(entry.forge.forge))
                    if entry.forge.forge is not UNSET
                    else entry.forge,
                    WORK_SOURCE_FORGE_RULES,
                    provider=provider,
                    current=current,
                    section=WORK_SOURCES_SECTION,
                    index=index,
                )
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(WORK_SOURCES_SECTION, index, exc) from exc
            forge_id = current if resolved is None else resolved.forge.forge_id
            assert forge_id is not None
            warnings.extend(() if resolved is None else resolved.warnings)
            work_sources.append(
                WorkSourceDeclaration(
                    slug=entry.slug,
                    name=entry.name,
                    work_source_id=sources_held.ids[entry.slug],
                    fields=entry.draft.on(forge_id),
                    edit=entry.edit if resolved is None else replace(entry.edit, forge_id=forge_id),
                )
            )
        repositories: list[RepositoryDeclaration] = []
        for index, repo in enumerate(entries.repositories):
            stored_repo = held.stored.get(repo.slug)
            current = None if stored_repo is None else stored_repo.fields.forge_id
            try:
                resolved = planner.decide(
                    replace(repo.forge, named=forges.resolve(repo.forge.forge))
                    if repo.forge.forge is not UNSET
                    else repo.forge,
                    REPOSITORY_FORGE_RULES,
                    provider=None,
                    current=current,
                    section=REPOSITORIES_SECTION,
                    index=index,
                )
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(REPOSITORIES_SECTION, index, exc) from exc
            forge_id = current if resolved is None else resolved.forge.forge_id
            assert forge_id is not None
            warnings.extend(() if resolved is None else resolved.warnings)
            repositories.append(
                RepositoryDeclaration(
                    slug=repo.slug,
                    name=repo.name,
                    repository_id=held.ids[repo.slug],
                    fields=repo.draft.on(forge_id),
                    edit=repo.edit if resolved is None else replace(repo.edit, forge_id=forge_id),
                )
            )
        return ConfigDeclaration(
            secrets=entries.secrets,
            forges=tuple(planner.planned),
            work_sources=tuple(work_sources),
            repositories=tuple(repositories),
            projects=projects.section,
            scopes=tuple(
                ScopeDeclaration(
                    name=scope.name,
                    description=scope.description,
                    edit=scope.edit,
                    project_id=projects.project_of(SCOPES_SECTION, index, scope.project).project_id,
                )
                for index, scope in enumerate(entries.scopes)
            ),
            routines=tuple(
                RoutineDeclaration(
                    name=routine.name,
                    graph_name=routine.graph_name,
                    default_scope_slug=routine.default_scope_slug,
                    default_model=routine.default_model,
                    default_effort=routine.default_effort,
                    default_harnesses=routine.default_harnesses,
                    edit=routine.edit,
                    project_id=projects.project_of(ROUTINES_SECTION, index, routine.project).project_id,
                    scopes=routine.scopes,
                )
                for index, routine in enumerate(entries.routines)
            ),
        ), tuple(warnings)

    def _stored(
        self,
        declaration: ConfigDeclaration,
        held: _Repositories,
        sources_held: _WorkSources,
        forges: dict[str, ConfiguredForge],
        former_forges: dict[str, ConfiguredForge],
    ) -> StoredConfig:
        secrets = self._secrets.get_many(list(declaration.secrets))
        declared_slugs = {d.slug for d in declaration.repositories}
        source_slugs = {d.slug for d in declaration.work_sources}
        retired = self._secrets.retired_names(list(secrets))
        scopes: dict[tuple[str, str], DeclarableRecord] = {
            (scope.project_id, scope.slug): scope for scope in self._scopes.list_all()
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
            for name, routine in self._routines.get_many_by_name(project_id, project_names).items()
        }
        ids = {key: r.routine_id for key, r in routines.items() if isinstance(r, Routine)}
        scope_links = self._routine_scopes.list_scopes_for(list(ids.values()))
        linked = {key: tuple(scope_links[routine_id]) for key, routine_id in ids.items()}
        declared_scopes = [d for d in declaration.scopes if isinstance(d, ScopeDeclaration)]
        return StoredConfig(
            forges=forges,
            former_forges=former_forges,
            work_sources={slug: sources_held.stored[slug] for slug in source_slugs if slug in sources_held.stored},
            former_work_sources={
                slug: sources_held.former[slug] for slug in source_slugs if slug in sources_held.former
            },
            repositories={slug: held.stored[slug] for slug in declared_slugs if slug in held.stored},
            former_repositories={slug: held.former[slug] for slug in declared_slugs if slug in held.former},
            secrets={name: RecordState.of(name in retired) for name in secrets},
            scopes={d.key: scopes[d.key] for d in declared_scopes if d.key in scopes},
            routines=routines,
            references=DeclaredReferences(
                scopes=frozenset(scopes) | {d.key for d in declared_scopes},
                enabled_graphs=frozenset(self._graphs.enabled_names(sorted(graph_names))),
                linked_scopes=linked,
            ),
        )
