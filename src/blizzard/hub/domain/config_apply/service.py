"""``ConfigApplication`` — the use case behind a declarative apply: it loads what the entries name, mints the ids and
the instant they write under, plans the ``projects`` section and the urgency sections, and hands the whole
declaration to :class:`~blizzard.hub.domain.config.authoring.ConfigAuthoring` to reconcile (``bzh:config-apply``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field, replace
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.cadence.declarations import CadenceDeclaration
from blizzard.hub.domain.cadence.model import Cadence, ChoreShape, chore_urgencies_named_beyond
from blizzard.hub.domain.cadence.ports import IReadCadenceRepository
from blizzard.hub.domain.config.apply import (
    CADENCES_SECTION,
    CREDENTIALS_SECTION,
    FORGES_SECTION,
    REPOSITORIES_SECTION,
    ROUTINES_SECTION,
    SCOPES_SECTION,
    WORK_SOURCES_SECTION,
    ApplyEntryRefused,
    ApplyPlan,
    ConfigDeclaration,
    CredentialDeclaration,
    DeclaredForgeFacts,
    DeclaredOldField,
    DeclaredReferences,
    EntryOutcome,
    ForgeDeclaration,
    RepositoryDeclaration,
    StoredConfig,
    UrgencyReferenceGuard,
    WorkSourceDeclaration,
    require_appliable_forges,
)
from blizzard.hub.domain.config.authoring import ConfigAuthoring
from blizzard.hub.domain.config.changes import ChangeContext, Door, RecordState
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.credential_resolution import (
    CredentialChoice,
    available_credential,
    decide_credential,
    held_for_old_field,
)
from blizzard.hub.domain.config.credentials import (
    ConfiguredCredential,
    CredentialDraft,
    CredentialSlug,
    IReadCredentialRepository,
    PlannedCredentials,
    binding_named,
    require_declarable_credential,
)
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import ForgeChoice, resolve_forge
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeId,
    ForgeSlug,
    ForgeUnavailable,
    IReadForgeRepository,
    PlannedForges,
)
from blizzard.hub.domain.config.records import NamedRetirableConfiguredRecord
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
from blizzard.hub.domain.config_apply.entries import (
    DeclaredCadence,
    DeclaredCredential,
    DeclaredRoutine,
    DocumentEntries,
)
from blizzard.hub.domain.garden.declarations import RoutineDeclaration, ScopeDeclaration
from blizzard.hub.domain.garden.routines import (
    DEFAULT_URGENCY_FIELD,
    IReadRoutineRepository,
    IReadRoutineScopeRepository,
    Routine,
    stated_handle,
)
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
from blizzard.hub.domain.projects.order import IReadProjectPositions
from blizzard.hub.domain.urgency.declarations import StoredUrgencies, UrgencyWrite, plan_urgencies
from blizzard.hub.domain.urgency.model import Urgency, UrgencyId, require_live
from blizzard.hub.domain.urgency.ports import (
    IReadTenantSettings,
    IReadUrgencyOrder,
    IReadUrgencyRepository,
    urgency_lock,
)
from blizzard.hub.domain.urgency.services import TenantSettingsMissing


@domain_model
@dataclass(frozen=True)
class ApplyResult:
    """What an apply decided: the id its changes share — ``None`` on a dry run, which commits nothing — and an outcome
    row per named record."""

    apply_id: str | None
    outcomes: tuple[EntryOutcome, ...]


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


class _UrgencyNames:
    """The urgencies the routine and cadence entries may name, by slug: every stored one, read on the first entry
    naming one, and each the document creates, under the id the plan minted for it. :meth:`id_of` resolves an entry's
    slug at the apply's edge, so no declaration holds one."""

    def __init__(self, urgencies: IReadUrgencyRepository, plan: ApplyPlan) -> None:
        self._urgencies = urgencies
        self._created = {
            write.record.urgency.slug: write.record.urgency
            for write in plan.writes
            if isinstance(write.record, UrgencyWrite) and write.from_revision is None
        }
        self._by_slug: dict[str, Urgency] | None = None

    def id_of(self, section: str, index: int, field: str, slug: str) -> UrgencyId:
        """The live urgency ``slug`` names on the entry's ``field``; the entry is refused ``urgency_unknown`` or
        ``urgency_retired`` otherwise."""
        if self._by_slug is None:
            self._by_slug = {u.slug: u for u in self._urgencies.list_all()} | self._created
        try:
            return require_live(field, slug, self._by_slug.get(slug)).urgency_id
        except ConfigFieldError as exc:
            raise ApplyEntryRefused(section, index, exc) from exc

    def guards(self, section: str, index: int, named: set[UrgencyId]) -> list[UrgencyReferenceGuard]:
        """A guard for each stored urgency of ``named`` — one the document creates needs none."""
        stored = {u.urgency_id: u for u in (self._by_slug or {}).values() if u.slug not in self._created}
        return [
            UrgencyReferenceGuard(section, index, urgency_id, stored[urgency_id].slug, stored[urgency_id].revision)
            for urgency_id in sorted(named)
            if urgency_id in stored
        ]


class _HeldCredentials:
    """Every credential of the tenant, retired ones included, read once, on the first entry needing them."""

    def __init__(self, credentials: IReadCredentialRepository) -> None:
        self._credentials = credentials
        self._held: list[ConfiguredCredential] | None = None

    def all(self) -> list[ConfiguredCredential]:
        if self._held is None:
            self._held = self._credentials.list_all(include_retired=True)
        return self._held


class _OldFieldCredentials:
    """The credential each old secret field the referrer entries resolve names, decided at the apply's edge: the one on
    its forge acting as its ``github.token`` authority, else one fresh id per forge and secret, for the credential the
    apply creates. Each is collected as a :class:`DeclaredOldField` for the apply's unit to resolve under its locks."""

    def __init__(self, held: _HeldCredentials, clock: IClock) -> None:
        self._held = held
        self._clock = clock
        self._minted: dict[tuple[ForgeId, str], CredentialId] = {}
        self.declared: list[DeclaredOldField] = []

    def planned(self, section: str, index: int, field: str, forge_id: ForgeId, secret_name: str) -> CredentialId:
        """The credential the entry at ``section`` and ``index`` names for its old ``field`` naming ``secret_name`` on
        ``forge_id``."""
        held = held_for_old_field(forge_id, secret_name, self._held.all())
        if held is not None:
            planned = held.credential_id
        else:
            key = (forge_id, secret_name)
            if key not in self._minted:
                self._minted[key] = CredentialId(Id.mint(IdPrefix.CREDENTIAL, self._clock).value)
            planned = self._minted[key]
        self.declared.append(DeclaredOldField(section, index, field, forge_id, secret_name, planned))
        return planned


@domain_model
@dataclass(frozen=True)
class _Credentials:
    """The credentials a document's apply meets: the declared ones, each under the id it takes; every credential a
    referrer entry may name, as the apply leaves it; the stored credential each declared slug names; and the one that
    left each declared slug none holds."""

    declarations: tuple[CredentialDeclaration, ...] = ()
    planned: PlannedCredentials = field(default_factory=lambda: PlannedCredentials((), {}))
    stored: dict[str, ConfiguredCredential] = field(default_factory=dict[str, ConfiguredCredential])
    former: dict[str, ConfiguredCredential] = field(default_factory=dict[str, ConfiguredCredential])


#: A document entry names no credential but by an old secret field.
_NO_CREDENTIAL = CredentialChoice()


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
        credentials: IReadCredentialRepository,
        projects: IReadProjectRepository,
        links: IReadProjectLinks,
        positions: IReadProjectPositions,
        scopes: IReadScopeRepository,
        routines: IReadRoutineRepository,
        routine_scopes: IReadRoutineScopeRepository,
        cadences: IReadCadenceRepository,
        graphs: IReadGraphRepository,
        secrets: ISecretCatalog,
        tenant_id: str,
        urgencies: IReadUrgencyRepository,
        urgency_order: IReadUrgencyOrder,
        tenant_settings: IReadTenantSettings,
        pick: ColorPick,
        clock: IClock,
    ) -> None:
        self._authoring = authoring
        self._repositories = repositories
        self._work_sources = work_sources
        self._forges = forges
        self._credentials = credentials
        self._projects = projects
        self._links = links
        self._positions = positions
        self._scopes = scopes
        self._routines = routines
        self._routine_scopes = routine_scopes
        self._cadences = cadences
        self._graphs = graphs
        self._secrets = secrets
        self._tenant_id = tenant_id
        self._urgencies = urgencies
        self._urgency_order = urgency_order
        self._tenant_settings = tenant_settings
        self._pick = pick
        self._clock = clock

    def apply(self, entries: DocumentEntries, *, actor: str, dry_run: bool, may_admit: bool) -> ApplyResult:
        """Apply ``entries`` as ``actor`` through the apply door. :class:`ApplyEntryRefused` names the entry that
        refused, and the store is left as it was; a planned write leaving a cadence admitting work raises
        :class:`~blizzard.hub.domain.cadence.model.CadenceAdmissionRefused` unless ``may_admit``."""
        apply_id = None if dry_run else Id.mint(IdPrefix.CONFIG_APPLY, self._clock).value
        ctx = ChangeContext(actor=actor, door=Door.APPLY, apply_id=apply_id)
        repositories = self._held_repositories(entries)
        sources = self._held_work_sources(entries)
        projects = self._plan_projects(entries, ctx, repositories, sources)
        held_forges = {f.slug: f for f in self._forges.list_all(include_retired=True)}
        declared_forges, lookup, former_forges = self._held_forges(entries, held_forges, ctx)
        urgency_plan = self._plan_urgencies(entries, ctx)
        names = _UrgencyNames(self._urgencies, urgency_plan)
        held_credentials = _HeldCredentials(self._credentials)
        credentials = self._held_credentials(entries, held_credentials, lookup, ctx)
        old_fields = _OldFieldCredentials(held_credentials, self._clock)
        declaration = self._declaration(
            entries, projects, repositories, sources, lookup, credentials.planned, names, old_fields
        )
        cadences, held_cadences = self._cadence_declarations(entries, projects, names, may_admit=may_admit)
        declaration = replace(
            declaration,
            declared_forges=declared_forges,
            credentials=credentials.declarations,
            cadences=cadences,
            urgencies=urgency_plan,
        )
        stored = self._stored(declaration, repositories, sources, held_forges, former_forges, held_cadences)
        stored = replace(
            stored,
            credentials=credentials.stored,
            former_credentials=credentials.former,
            acting_credentials={c.credential_id: c for c in held_credentials.all()} if entries.repositories else {},
        )
        guards = _reference_guards(declaration, stored, names)
        declaration = replace(
            declaration,
            reference_guards=guards,
            reference_locks=tuple(sorted({urgency_lock(UrgencyId(guard.urgency_id)) for guard in guards})),
        )
        outcomes = self._authoring.apply(declaration, stored, ctx, dry_run=dry_run)
        return ApplyResult(apply_id, tuple(outcomes))

    def _held_forges(
        self, entries: DocumentEntries, held: dict[str, ConfiguredForge], ctx: ChangeContext
    ) -> tuple[tuple[ForgeDeclaration, ...], PlannedForges, dict[str, ConfiguredForge]]:
        former: dict[str, ConfiguredForge] = {}
        for entry in entries.forges:
            if entry.slug not in held and (slug := ForgeSlug.of(entry.slug)) is not None:
                left = self._forges.get_by_former_slug(slug)
                if left is not None:
                    former[entry.slug] = left
        at = self._clock.now()
        require_appliable_forges(
            [DeclaredForgeFacts(e.slug, e.kind, e.fields, e.edit) for e in entries.forges], held, former
        )
        projected = dict(held)
        declarations: list[ForgeDeclaration] = []
        for index, entry in enumerate(entries.forges):
            try:
                stored = held.get(entry.slug)
                if stored is None:
                    forge_id = ForgeId(Id.mint(IdPrefix.FORGE, self._clock).value)
                    record, _ = ConfiguredForge.new(
                        entry.slug, entry.name, entry.kind, entry.fields, ctx, at=at, forge_id=forge_id
                    )
                else:
                    forge_id = stored.forge_id
                    enabled = stored.set_retired(False, ctx, if_match=None, at=at) if stored.retired else None
                    record = stored if enabled is None else enabled[0]
                    edited = record.edit(entry.edit, ctx, if_match=None, at=at)
                    record = record if edited is None else edited[0]
                projected[entry.slug] = record
                declarations.append(
                    ForgeDeclaration(entry.slug, entry.name, entry.kind, entry.fields, entry.edit, forge_id)
                )
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(FORGES_SECTION, index, exc) from exc
        return tuple(declarations), PlannedForges(tuple(projected.values()), former), former

    def _held_credentials(
        self, entries: DocumentEntries, held: _HeldCredentials, forges: PlannedForges, ctx: ChangeContext
    ) -> _Credentials:
        """The credentials the entries meet: each declared one on the forge it names, under the id it takes — a stored
        record's, or one minted for a credential the document creates — and every credential a referrer entry may
        name, as the document leaves it. Nothing is read for a document declaring and naming none."""
        named = [
            entry.credential
            for entry in (*entries.work_sources, *entries.repositories)
            if not isinstance(entry.credential, UnsetType)
        ]
        if not entries.credentials and not named:
            return _Credentials()
        by_slug = {c.slug: c for c in held.all()}
        former: dict[str, ConfiguredCredential] = {}
        for value in sorted({e.slug for e in entries.credentials} - by_slug.keys()):
            if (slug := CredentialSlug.of(value)) is not None and (left := self._credentials.get_by_former_slug(slug)):
                former[value] = left
        at = self._clock.now()
        projected = {c.credential_id: c for c in held.all()}
        declarations: list[CredentialDeclaration] = []
        for index, entry in enumerate(entries.credentials):
            try:
                declaration, record = self._declared_credential(entry, by_slug, former, forges, ctx, at=at)
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(CREDENTIALS_SECTION, index, exc) from exc
            projected[record.credential_id] = record
            declarations.append(declaration)
        declared = {d.slug: former[d.slug] for d in declarations if d.slug in former}
        for value in sorted(set(named)):
            if any(value in (c.credential_id, c.slug) for c in projected.values()) or value in former:
                continue
            if (slug := CredentialSlug.of(value)) is not None and (left := self._credentials.get_by_former_slug(slug)):
                former[value] = left
        return _Credentials(
            declarations=tuple(declarations),
            planned=PlannedCredentials(tuple(projected.values()), former),
            stored={d.slug: by_slug[d.slug] for d in declarations if d.slug in by_slug},
            former=declared,
        )

    def _declared_credential(
        self,
        entry: DeclaredCredential,
        held: dict[str, ConfiguredCredential],
        former: dict[str, ConfiguredCredential],
        forges: PlannedForges,
        ctx: ChangeContext,
        *,
        at: datetime,
    ) -> tuple[CredentialDeclaration, ConfiguredCredential]:
        """The declaration of one credential entry, on the forge it names, and the credential as the apply leaves it."""
        forge = forges.resolve(entry.forge)
        if forge is None:
            raise ForgeUnavailable("forge", f"forge {entry.forge} is unknown")
        stored = held.get(entry.slug)
        require_declarable_credential(entry.slug, forge.forge_id, entry.binding, stored, former.get(entry.slug))
        draft = CredentialDraft(
            slug=entry.slug,
            name=entry.name,
            forge_id=forge.forge_id,
            binding=entry.binding,
            secret_name=entry.secret,
            settings=entry.settings,
        )
        if stored is None:
            credential_id = CredentialId(Id.mint(IdPrefix.CREDENTIAL, self._clock).value)
            record, _ = ConfiguredCredential.new(
                draft, binding_named(entry.binding), ctx, at=at, credential_id=credential_id
            )
        else:
            credential_id = stored.credential_id
            enabled = stored.set_retired(False, ctx, if_match=None, at=at) if stored.retired else None
            record = stored if enabled is None else enabled[0]
            edited = record.edit(entry.edit, binding_named(record.fields.binding), ctx, if_match=None, at=at)
            record = record if edited is None else edited[0]
        return CredentialDeclaration(draft, entry.edit, credential_id), record

    def _held_repositories(self, entries: DocumentEntries) -> _Repositories:
        stored = {r.slug: r for r in self._repositories.list_all(include_retired=True)}
        named = {entry.slug for entry in entries.repositories} | {
            reference
            for entry in entries.projects
            for reference in (
                *(entry.repositories or ()),
                *(entry.landing_order or ()),
                *(entry.repository_branches or {}),
            )
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
            entry.project
            for entry in (*entries.scopes, *entries.routines, *entries.cadences)
            if entry.project is not None
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
        ordering: list[ProjectId] = []
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
            if entry.landing_order is not None:
                ordering.append(held)
            if entry.repositories is None and (
                entry.landing_order is not None or entry.repository_branches is not None
            ):
                repository_projects.append(held)
            if entry.repositories is not None:
                repository_projects.append(held)
                listed_repositories.extend(
                    (held, ref.repository_id)
                    for reference in entry.repositories
                    if (ref := planned.resolve(reference)) is not None
                )
        # Read before the links that carry the positions: a landing-order fact landing between the two leaves the
        # sequence stale, so the apply is refused rather than appending a diff planned on positions it never read.
        landing_seqs = self._positions.landing_seqs(ordering)
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
            landing_seqs=landing_seqs,
        )
        return plan_projects(list(entries.projects), stored, ctx, pick=self._pick, at=self._clock.now())

    def _plan_urgencies(self, entries: DocumentEntries, ctx: ChangeContext) -> ApplyPlan:
        """The planned urgency sections, from the tenant's urgencies, their order — read first, so a position landing
        after it leaves the sequence stale — and its settings; nothing is read for a document stating none."""
        if not entries.urgencies.is_stated():
            return ApplyPlan()
        order = self._urgency_order.order()
        settings = self._tenant_settings.get()
        if settings is None:
            raise TenantSettingsMissing
        stored = StoredUrgencies(self._tenant_id, tuple(self._urgencies.list_all()), order, settings)
        return plan_urgencies(entries.urgencies, stored, ctx, pick=self._pick, at=self._clock.now())

    @staticmethod
    def _declaration(
        entries: DocumentEntries,
        projects: ProjectsPlan,
        held: _Repositories,
        sources_held: _WorkSources,
        forges: PlannedForges,
        credentials: PlannedCredentials,
        names: _UrgencyNames,
        old_fields: _OldFieldCredentials,
    ) -> ConfigDeclaration:
        """The domain's declarations from the entries: each record under the id it takes, on the forge it names and
        acting with the credential its old secret field resolves to — decided by the old-field table as every door
        decides it (:func:`decide_credential`) — and each scope and routine within the project it names, as the apply
        leaves the projects. An entry naming a credential acts with it, as the apply leaves it, and holds its secret.
        An entry's :class:`ConfigFieldError` on its forge or credential is refused at the entry."""

        def acting(
            section: str,
            index: int,
            credential: str | UnsetType,
            forge_id: ForgeId,
            *,
            field: str,
            sent: str | None | UnsetType,
            current: str | None | UnsetType,
            forge_moved: bool,
        ) -> tuple[CredentialId, str] | None:
            """The credential an entry acts with and its secret: the one it names, else the one its old field resolves
            to — ``field`` names it, ``sent`` is the value the entry sends and ``current`` the stored record's
            (:data:`UNSET` for a create); ``None`` leaves the stored record's. Refused at the entry when the named one
            is unavailable."""
            decision = decide_credential(
                _NO_CREDENTIAL, sent, current=current, field=field, required=False, forge_moved=forge_moved
            )
            held: tuple[CredentialId, str] | None = None
            if decision.resolve is not None:
                held = (old_fields.planned(section, index, field, forge_id, decision.resolve), decision.resolve)
            if isinstance(credential, UnsetType):
                return held
            try:
                use = available_credential(credential, credentials.resolve(credential))
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(section, index, exc) from exc
            return use.credential_id, use.fields.secret_name

        def resolved(choice: ForgeChoice, provider: str | None, current: ForgeId | None) -> ConfiguredForge | None:
            return resolve_forge(
                replace(choice, named=forges.resolve(choice.forge)) if choice.forge is not UNSET else choice,
                provider=provider,
                current=None if current is None else forges.resolve(current),
                creating=current is None,
                forges=lambda: forges.records,
            )

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
                forge = resolved(entry.forge, provider, current)
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(WORK_SOURCES_SECTION, index, exc) from exc
            forge_id = current if forge is None else forge.forge_id
            assert forge_id is not None
            fields = entry.draft.on(forge_id)
            edit = entry.edit if forge is None else replace(entry.edit, forge_id=forge_id)
            held_credential = acting(
                WORK_SOURCES_SECTION,
                index,
                entry.credential,
                forge_id,
                field="secret",
                sent=(UNSET if entry.draft.secret is None else entry.draft.secret)
                if stored is None
                else entry.edit.secret,
                current=UNSET if stored is None else stored.fields.secret,
                forge_moved=stored is not None and forge_id != stored.fields.forge_id,
            )
            if held_credential is not None:
                credential_id, secret = held_credential
                fields = entry.draft.on(forge_id, credential_id=credential_id, secret=secret)
                edit = replace(edit, credential_id=credential_id, secret=secret)
            work_sources.append(
                WorkSourceDeclaration(
                    slug=entry.slug,
                    name=entry.name,
                    work_source_id=sources_held.ids[entry.slug],
                    fields=fields,
                    edit=edit,
                )
            )
        repositories: list[RepositoryDeclaration] = []
        for index, repo in enumerate(entries.repositories):
            stored_repo = held.stored.get(repo.slug)
            current = None if stored_repo is None else stored_repo.fields.forge_id
            try:
                forge = resolved(repo.forge, None, current)
            except ConfigFieldError as exc:
                raise ApplyEntryRefused(REPOSITORIES_SECTION, index, exc) from exc
            forge_id = current if forge is None else forge.forge_id
            assert forge_id is not None
            repo_fields = repo.draft.on(forge_id)
            repo_edit = repo.edit if forge is None else replace(repo.edit, forge_id=forge_id)
            held_credential = acting(
                REPOSITORIES_SECTION,
                index,
                repo.credential,
                forge_id,
                field="secret_name",
                sent=(UNSET if repo.draft.secret_name is None else repo.draft.secret_name)
                if stored_repo is None
                else repo.edit.secret_name,
                current=UNSET if stored_repo is None else stored_repo.fields.secret_name,
                forge_moved=stored_repo is not None and forge_id != stored_repo.fields.forge_id,
            )
            if held_credential is not None:
                credential_id, secret_name = held_credential
                repo_fields = repo.draft.on(forge_id, credential_id=credential_id, secret_name=secret_name)
                repo_edit = replace(repo_edit, credential_id=credential_id, secret_name=secret_name)
            repositories.append(
                RepositoryDeclaration(
                    slug=repo.slug,
                    name=repo.name,
                    repository_id=held.ids[repo.slug],
                    fields=repo_fields,
                    edit=repo_edit,
                )
            )
        return ConfigDeclaration(
            secrets=entries.secrets,
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
                ConfigApplication._routine_declaration(index, routine, projects, names)
                for index, routine in enumerate(entries.routines)
            ),
            old_fields=tuple(old_fields.declared),
        )

    @staticmethod
    def _routine_declaration(
        index: int, routine: DeclaredRoutine, projects: ProjectsPlan, names: _UrgencyNames
    ) -> RoutineDeclaration:
        """The declaration of one routine entry, its default urgency resolved; an entry stating neither slug nor name
        is refused at the entry."""
        try:
            stated_handle(routine.slug, routine.name)
        except ConfigFieldError as exc:
            raise ApplyEntryRefused(ROUTINES_SECTION, index, exc) from exc
        slug = routine.default_urgency
        urgency_id = (
            slug if slug is None or slug is UNSET else names.id_of(ROUTINES_SECTION, index, DEFAULT_URGENCY_FIELD, slug)
        )
        return RoutineDeclaration(
            slug=routine.slug,
            name=routine.name,
            graph_name=routine.graph_name,
            default_scope_slug=routine.default_scope_slug,
            default_model=routine.default_model,
            default_effort=routine.default_effort,
            default_harnesses=routine.default_harnesses,
            edit=routine.edit if urgency_id is UNSET else replace(routine.edit, default_urgency_id=urgency_id),
            project_id=projects.project_of(ROUTINES_SECTION, index, routine.project).project_id,
            scopes=routine.scopes,
            default_urgency_id=None if urgency_id is UNSET else urgency_id,
        )

    def _cadence_declarations(
        self, entries: DocumentEntries, projects: ProjectsPlan, names: _UrgencyNames, *, may_admit: bool
    ) -> tuple[tuple[CadenceDeclaration, ...], dict[tuple[str, str], Cadence]]:
        """Each cadence entry within the project it names, its chores' urgencies resolved, beside the revision of the
        stored cadence it names, and those stored cadences by key — one read per project the entries name."""
        keyed = [
            (projects.project_of(CADENCES_SECTION, index, entry.project).project_id, _resolved(index, entry, names))
            for index, entry in enumerate(entries.cadences)
        ]
        held: dict[tuple[str, str], Cadence] = {}
        for project_id in sorted({project_id for project_id, _ in keyed}):
            held.update({(project_id, c.slug): c for c in self._cadences.list_all(project_id)})
        declarations = tuple(
            CadenceDeclaration(
                shape=entry.shape,
                edit=entry.edit,
                project_id=project_id,
                may_admit=may_admit,
                stored_revision=stored.revision if (stored := held.get((project_id, entry.shape.slug or ""))) else None,
            )
            for project_id, entry in keyed
        )
        return declarations, held

    def _stored(
        self,
        declaration: ConfigDeclaration,
        held: _Repositories,
        sources_held: _WorkSources,
        forges: dict[str, ConfiguredForge],
        former_forges: dict[str, ConfiguredForge],
        cadences: dict[tuple[str, str], Cadence],
    ) -> StoredConfig:
        secrets = self._secrets.get_many(list(declaration.secrets))
        declared_slugs = {d.slug for d in declaration.repositories}
        source_slugs = {d.slug for d in declaration.work_sources}
        retired = self._secrets.retired_names(list(secrets))
        scopes: dict[tuple[str, str], NamedRetirableConfiguredRecord] = {
            (scope.project_id, scope.slug): scope for scope in self._scopes.list_all()
        }
        graph_names: set[str] = set()
        names: dict[str, list[str]] = {}
        for entry in declaration.routines:
            assert isinstance(entry, RoutineDeclaration)
            graph_names.add(entry.graph_name)
            names.setdefault(entry.project_id, []).append(entry.handle)
        routines: dict[tuple[str, str], NamedRetirableConfiguredRecord] = {
            (project_id, name): routine
            for project_id, project_names in names.items()
            for name, routine in self._routines.get_many_by_slug(project_id, project_names).items()
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
            cadences=dict(cadences),
            references=DeclaredReferences(
                scopes=frozenset(scopes) | {d.key for d in declared_scopes},
                enabled_graphs=frozenset(self._graphs.enabled_names(sorted(graph_names))),
                linked_scopes=linked,
            ),
        )


def _resolved(index: int, entry: DeclaredCadence, names: _UrgencyNames) -> DeclaredCadence:
    """``entry`` with the urgency each chore names resolved into its create shape and, when it states chores, its
    edit."""
    if not any(entry.chore_urgencies):
        return entry
    ids = [
        None if slug is None else names.id_of(CADENCES_SECTION, index, f"chores[{position}].urgency", slug)
        for position, slug in enumerate(entry.chore_urgencies)
    ]

    def named(chores: Sequence[ChoreShape]) -> tuple[ChoreShape, ...]:
        return tuple(replace(chore, urgency_id=urgency_id) for chore, urgency_id in zip(chores, ids, strict=True))

    edit = entry.edit if entry.edit.chores is UNSET else replace(entry.edit, chores=named(entry.edit.chores))
    return replace(entry, shape=replace(entry.shape, chores=named(entry.shape.chores)), edit=edit)


def _reference_guards(
    declaration: ConfigDeclaration, stored: StoredConfig, names: _UrgencyNames
) -> tuple[UrgencyReferenceGuard, ...]:
    """A guard for each stored urgency a routine's or a cadence's write newly names — one its stored record does not
    already name. A reference the write leaves is the record's revision's to guard, since a retire clearing it moves
    that revision."""
    guards: list[UrgencyReferenceGuard] = []
    for index, routine in enumerate(declaration.routines):
        assert isinstance(routine, RoutineDeclaration)
        held = stored.routines.get(routine.key)
        named = routine.default_urgency_id if held is None else routine.edit.default_urgency_id
        before = held.default_urgency_id if isinstance(held, Routine) else None
        if isinstance(named, str) and named != before:
            guards.extend(names.guards(ROUTINES_SECTION, index, {named}))
    for index, cadence in enumerate(declaration.cadences):
        assert isinstance(cadence, CadenceDeclaration)
        held = stored.cadences.get(cadence.key)
        chores = cadence.shape.chores if held is None else cadence.edit.chores
        named = chore_urgencies_named_beyond(
            [c.urgency_id for c in (() if chores is UNSET else chores)],
            (c.spec.urgency_id for c in (held.chores if isinstance(held, Cadence) else ())),
        )
        guards.extend(names.guards(CADENCES_SECTION, index, set(named.values())))
    return tuple(guards)
