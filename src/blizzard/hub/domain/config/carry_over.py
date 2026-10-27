"""Carry-over — a file-configured hub's legacy keys imported into its records, once.

The planner decides from loaded data alone: the parsed :class:`LegacyKeys`, the values of the variables it
names, the commit coordinates the hub has delivered to, and the records the store already holds. The store
wins over the file: a record that already exists, active or retired, is skipped, never overwritten. Each work source
and repository the import creates is linked to the project it lands in, which the importing layer decides. The
writer commits the plan, its links and the ``config_import`` fact as one transaction, so a refusal writes nothing.

Each address the keys name resolves to the tenant's forge holding its canonical origin, or to a forge the import makes
for it exactly as the forges revision made one, recorded under :data:`MIGRATION_CONTEXT` like every other row it
writes — so a ``BZ_FORGE_URL`` spelled differently from a stored forge's origin resolves to that forge."""

from __future__ import annotations

from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from pathlib import Path
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.config import secret_lifecycle
from blizzard.hub.domain.config.apply import REPOSITORIES_SECTION, WORK_SOURCES_SECTION, LinkRecord
from blizzard.hub.domain.config.changes import ChangeContext, ConfigChange, Door, RecordKind
from blizzard.hub.domain.config.errors import ConfigFieldError
from blizzard.hub.domain.config.forge_resolution import (
    REPOSITORY_FORGE_RULES,
    WORK_SOURCE_FORGE_RULES,
    ForgeChoice,
    ForgePlanner,
)
from blizzard.hub.domain.config.forges import ConfiguredForge
from blizzard.hub.domain.config.legacy_keys import (
    ENV_FORGE_BASE_BRANCH,
    ENV_FORGE_OWNER,
    ENV_FORGE_TOKEN,
    ENV_FORGE_URL,
    LegacyKeys,
)
from blizzard.hub.domain.config.repositories import (
    CommitOrigin,
    ConfiguredRepository,
    RepositoryDraft,
    RepositoryId,
    RepositoryName,
)
from blizzard.hub.domain.config.secrets import SealedValue, SecretName, SecretNameError
from blizzard.hub.domain.config.work_sources import ConfiguredWorkSource, WorkSourceDraft, WorkSourceId, WorkSourceName
from blizzard.hub.domain.kernel.slugs import derive_slug

#: Every row the import writes is attributed to this actor, through this door.
MIGRATION_CONTEXT = ChangeContext(actor="migration", door=Door.MIGRATION)

#: The owner a repository takes when neither its origin nor ``BZ_FORGE_OWNER`` names one — the import's alone.
DEFAULT_OWNER = "blizzard"
DEFAULT_BASE_BRANCH = "main"


class LegacyImportRefused(Exception):
    """The import cannot run as the keys stand; names the variable or block to fix. Nothing is written."""


class LegacyStartRefused(Exception):
    """The hub may not start while it carries legacy keys; names the remedy, never a value."""


def secret_name_of(variable: str) -> str:
    """The secret a legacy variable becomes: lowercased, ``_`` turned to ``-``."""
    return variable.lower().replace("_", "-")


@domain_model
@dataclass(frozen=True)
class CommitCoordinate:
    """One distinct ``(forge, repo)`` among the hub's ``git_commit`` artifacts; ``forge`` is the origin URL."""

    forge: str | None
    repo: str


@domain_model
@dataclass(frozen=True)
class ExistingConfig:
    """The records the store already holds, retired ones included."""

    secrets: frozenset[str]
    work_sources: tuple[ConfiguredWorkSource, ...]
    repositories: tuple[ConfiguredRepository, ...]
    forges: tuple[ConfiguredForge, ...] = ()


@domain_model
@dataclass(frozen=True)
class PlannedSecret:
    """A secret to create from ``variable``'s value, sealed before the write."""

    name: SecretName
    variable: str
    change: ConfigChange


@domain_model
@dataclass(frozen=True)
class ImportRead:
    """What an import read — names only, never a value."""

    config_path: str
    sources: tuple[str, ...]
    variables: tuple[str, ...]


@domain_model
@dataclass(frozen=True)
class ConfigImportFact:
    imported_at: datetime
    actor: str
    read: ImportRead


@domain_model
@dataclass(frozen=True)
class ImportOutcome:
    """One record the import named: ``created``, or skipped because the store already held it."""

    kind: RecordKind
    key: str
    created: bool


@domain_model
@dataclass(frozen=True)
class ImportPlan:
    secrets: tuple[PlannedSecret, ...]
    forges: tuple[tuple[ConfiguredForge, ConfigChange], ...]
    work_sources: tuple[tuple[ConfiguredWorkSource, ConfigChange], ...]
    repositories: tuple[tuple[ConfiguredRepository, ConfigChange], ...]
    fact: ConfigImportFact
    outcomes: tuple[ImportOutcome, ...]


@domain_model
@dataclass(frozen=True)
class SealedSecretWrite:
    name: str
    sealed: SealedValue
    change: ConfigChange


@domain_model
@dataclass(frozen=True)
class LegacyImport:
    """Every write of one import, in commit order: secrets and forges, then the records naming them, then their links —
    each a link record with its change — then the fact."""

    secrets: tuple[SealedSecretWrite, ...]
    forges: tuple[tuple[ConfiguredForge, ConfigChange], ...]
    work_sources: tuple[tuple[ConfiguredWorkSource, ConfigChange], ...]
    repositories: tuple[tuple[ConfiguredRepository, ConfigChange], ...]
    fact: ConfigImportFact
    links: tuple[tuple[LinkRecord, ConfigChange], ...] = ()


class ImportStatus(StrEnum):
    IMPORTED = "imported"
    ALREADY_IMPORTED = "already-imported"
    NOTHING_TO_IMPORT = "nothing-to-import"


@domain_model
@dataclass(frozen=True)
class ImportResult:
    status: ImportStatus
    outcomes: tuple[ImportOutcome, ...] = ()


class IReadConfigImports(Protocol):
    def recorded(self) -> bool:
        """Whether a ``config_import`` fact exists."""
        ...

    def commit_coordinates(self) -> list[CommitCoordinate]:
        """The distinct ``(forge, repo)`` of every ``git_commit`` artifact; ``repo`` falls back to the artifact name."""
        ...


class IImportLinks(Protocol):
    """Links the records an import creates to the project they land in."""

    def links(
        self,
        work_sources: Sequence[WorkSourceId],
        repositories: Sequence[RepositoryId],
        ctx: ChangeContext,
        *,
        at: datetime,
    ) -> tuple[tuple[LinkRecord, ConfigChange], ...]:
        """Each link record with its change. :class:`LegacyImportRefused` when no project can be chosen."""
        ...


class IConfigImportWriter(IReadConfigImports, Protocol):
    def write(self, imported: LegacyImport) -> bool:
        """Commit every write and the fact in one transaction; ``False``, writing nothing, when an import
        was recorded first. :class:`LegacyImportRefused` when a write's in-transaction check refuses it."""
        ...


def _value(values: Mapping[str, str], variable: str) -> str:
    value = values.get(variable)
    if value is None or not value.strip():
        raise LegacyImportRefused(f"{variable} is unset or blank — set it to the credential it names, then re-run")
    return value


def _work_sources(
    legacy: LegacyKeys,
    existing: ExistingConfig,
    forges: ForgePlanner,
    ctx: ChangeContext,
    at: datetime,
    outcomes: list[ImportOutcome],
) -> tuple[tuple[ConfiguredWorkSource, ConfigChange], ...]:
    slugs = {record.slug for record in existing.work_sources}
    locators = {(record.fields.provider, record.fields.locator) for record in existing.work_sources}
    planned: list[tuple[ConfiguredWorkSource, ConfigChange]] = []
    for source in legacy.sources:
        forges.foresee(
            ForgeChoice(api_base=source.api_base, web_base=source.web_base), WORK_SOURCE_FORGE_RULES, creating=True
        )
    for source in legacy.sources:
        # The block's name becomes the source's display name, and the slug derived from it its handle.
        slug = derive_slug(source.name, fallback="source")
        if slug in slugs or (source.provider, source.repo) in locators:
            outcomes.append(ImportOutcome(RecordKind.WORK_SOURCE, slug, created=False))
            continue
        draft = WorkSourceDraft(
            provider=source.provider,
            locator=source.repo,
            annotate=source.annotate,
            secret=secret_name_of(source.token_env),
        )
        choice = ForgeChoice(api_base=source.api_base, web_base=source.web_base)
        try:
            resolved = forges.decide(
                choice,
                WORK_SOURCE_FORGE_RULES,
                provider=source.provider,
                current=None,
                section=WORK_SOURCES_SECTION,
                index=0,
            )
            assert resolved is not None
            fields = draft.on(resolved.forge.forge_id)
            planned.append(
                ConfiguredWorkSource.new(
                    slug, source.name if WorkSourceName.of(source.name) else None, fields, ctx, at=at
                )
            )
        except ConfigFieldError as exc:
            raise LegacyImportRefused(f'[[work_source]] "{source.name}": {exc}') from exc
        slugs.add(slug)
        outcomes.append(ImportOutcome(RecordKind.WORK_SOURCE, slug, created=True))
    return tuple(planned)


def _owner_repos(commits: Sequence[CommitCoordinate], values: Mapping[str, str]) -> list[tuple[str, str]]:
    """Each distinct ``(owner, repo)`` the commits name, in first-seen order."""
    owner_default = values.get(ENV_FORGE_OWNER, "").strip() or DEFAULT_OWNER
    derived: dict[tuple[str, str], None] = {}
    for commit in commits:
        origin = CommitOrigin.of(commit.forge, commit.repo)
        if origin.name:
            derived.setdefault((origin.owner or owner_default, origin.name), None)
    return list(derived)


def _repositories(
    commits: Sequence[CommitCoordinate],
    values: Mapping[str, str],
    existing: ExistingConfig,
    forges: ForgePlanner,
    ctx: ChangeContext,
    at: datetime,
    outcomes: list[ImportOutcome],
) -> tuple[tuple[ConfiguredRepository, ConfigChange], ...]:
    """A repository for each ``(owner, repo)`` the commits name that the store does not hold. Without
    ``BZ_FORGE_URL``, a pair is held when any repository records it; only an unheld pair needs the URL."""
    if not commits:
        return ()
    pairs = _owner_repos(commits, values)
    forge_api_url = values.get(ENV_FORGE_URL, "").strip()
    if not forge_api_url:
        held_pairs = {(r.fields.owner, r.fields.repo): r.slug for r in existing.repositories}
        if any(pair not in held_pairs for pair in pairs):
            raise LegacyImportRefused(
                f"{ENV_FORGE_URL} is unset — the hub has delivered commits, and their repositories need its forge URL"
            )
        outcomes.extend(ImportOutcome(RecordKind.REPOSITORY, held_pairs[pair], created=False) for pair in pairs)
        return ()
    base_branch = values.get(ENV_FORGE_BASE_BRANCH, "").strip() or DEFAULT_BASE_BRANCH
    try:
        resolved = forges.decide(
            ForgeChoice(api_base=forge_api_url),
            REPOSITORY_FORGE_RULES,
            provider=None,
            current=None,
            section=REPOSITORIES_SECTION,
            index=0,
        )
    except ConfigFieldError as exc:
        raise LegacyImportRefused(f"{ENV_FORGE_URL}: {exc.message}") from exc
    assert resolved is not None
    derived = [
        RepositoryDraft(
            owner=owner, repo=repo, base_branch=base_branch, secret_name=secret_name_of(ENV_FORGE_TOKEN)
        ).on(resolved.forge.forge_id)
        for owner, repo in pairs
    ]
    held = {(r.fields.forge_id, r.fields.owner, r.fields.repo): r.slug for r in existing.repositories}
    taken = {record.slug for record in existing.repositories}
    bare_counts: dict[str, int] = {}
    for fields in derived:
        bare_counts[fields.repo] = bare_counts.get(fields.repo, 0) + 1
    planned: list[tuple[ConfiguredRepository, ConfigChange]] = []
    for fields in derived:
        holder = held.get((fields.forge_id, fields.owner, fields.repo))
        if holder is not None:
            outcomes.append(ImportOutcome(RecordKind.REPOSITORY, holder, created=False))
            continue
        bare = derive_slug(fields.repo, fallback="repository")
        qualified = derive_slug(f"{fields.owner}-{fields.repo}", fallback="repository")
        slug = qualified if bare_counts[fields.repo] > 1 or bare in taken else bare
        if slug in taken:
            raise LegacyImportRefused(f"repository slug {slug} is already taken by another repository record")
        taken.add(slug)
        label = fields.repo if slug == bare else f"{fields.owner}-{fields.repo}"
        try:
            planned.append(
                ConfiguredRepository.new(slug, label if RepositoryName.of(label) else None, fields, ctx, at=at)
            )
        except ConfigFieldError as exc:
            raise LegacyImportRefused(f"repository {slug}: {exc}") from exc
        outcomes.append(ImportOutcome(RecordKind.REPOSITORY, slug, created=True))
    return tuple(planned)


def _secrets(
    legacy: LegacyKeys,
    values: Mapping[str, str],
    existing: ExistingConfig,
    *,
    forge_token: bool,
    ctx: ChangeContext,
    at: datetime,
) -> tuple[tuple[PlannedSecret, ...], list[ImportOutcome]]:
    """A secret for each distinct ``token_env`` variable, and for ``BZ_FORGE_TOKEN`` when ``forge_token``."""
    variables = [source.token_env for source in legacy.sources]
    if forge_token:
        variables.append(ENV_FORGE_TOKEN)
    planned: list[PlannedSecret] = []
    outcomes: list[ImportOutcome] = []
    for variable in dict.fromkeys(variables):
        try:
            name = SecretName.parse(secret_name_of(variable))
        except SecretNameError as exc:
            raise LegacyImportRefused(f"{variable} cannot name a secret: {exc}") from exc
        if name.value in existing.secrets:
            outcomes.append(ImportOutcome(RecordKind.SECRET, name.value, created=False))
            continue
        _value(values, variable)
        planned.append(PlannedSecret(name, variable, secret_lifecycle.creation(name, ctx, at=at)))
        outcomes.append(ImportOutcome(RecordKind.SECRET, name.value, created=True))
    return tuple(planned), outcomes


def plan_import(
    legacy: LegacyKeys,
    values: Mapping[str, str],
    commits: Sequence[CommitCoordinate],
    existing: ExistingConfig,
    *,
    ctx: ChangeContext,
    at: datetime,
) -> ImportPlan:
    """The writes that carry ``legacy`` into records, skipping what the store already holds.
    :class:`LegacyImportRefused` names the first variable or block the import cannot carry."""
    record_outcomes: list[ImportOutcome] = []
    forges = ForgePlanner(existing.forges, ctx, at=at)
    work_sources = _work_sources(legacy, existing, forges, ctx, at, record_outcomes)
    repositories = _repositories(commits, values, existing, forges, ctx, at, record_outcomes)
    forge_token = bool(repositories) or ENV_FORGE_TOKEN in values
    secrets, secret_outcomes = _secrets(legacy, values, existing, forge_token=forge_token, ctx=ctx, at=at)
    fact = ConfigImportFact(
        imported_at=at,
        actor=ctx.actor,
        read=ImportRead(
            config_path=str(legacy.config_path),
            sources=tuple(source.name for source in legacy.sources),
            variables=legacy.variables,
        ),
    )
    return ImportPlan(
        secrets=secrets,
        forges=tuple((planned.record, planned.change) for planned in forges.planned),
        work_sources=work_sources,
        repositories=repositories,
        fact=fact,
        outcomes=(*secret_outcomes, *record_outcomes),
    )


@domain_model
@dataclass(frozen=True)
class LegacyStart:
    """Whether a hub may start with the legacy keys it carries: never while any remain."""

    recorded: bool
    keys: LegacyKeys

    @classmethod
    def of(cls, imports: Iterable[IReadConfigImports], keys: LegacyKeys) -> LegacyStart:
        """``imports`` reads through each open tenant. The import is the hub's, recorded once into the one
        tenant it wrote, so any tenant's reader answers it; a hub with no open tenant has recorded none."""
        return cls(recorded=any(tenant.recorded() for tenant in imports), keys=keys)

    def check(self) -> None:
        """Refuse to start while a legacy key remains, naming the exact remedy — never a value."""
        if not self.keys.present():
            return
        if not self.recorded:
            root: Path = self.keys.config_path.parent
            raise LegacyStartRefused(
                "this hub is still configured by legacy keys — run "
                f"`blizzard hub config import-legacy --dir {root}`, then remove them and restart"
            )
        raise LegacyStartRefused(
            "the legacy configuration was imported, but these keys remain — remove each, then restart: "
            + "; ".join(self.keys.locations())
        )
