"""``ConfigAuthoring`` — the one writer of configured records.

Every verb takes the record the edge resolved (``bzh:domain-takes-objects``), asks the record's model for the change,
and hands it to the write repository with the record write. A write that changes nothing writes nothing. Secrets
and forges route through here too.

A repository or work-source write that names its forge the old way — by address — goes through the ``…_naming`` verbs:
the forge is resolved first (:func:`~blizzard.hub.domain.config.forge_resolution.decide_forge`), and a forge made for an
origin no forge holds is committed on its own before the record write, recorded under the same change context. A forge
left unnamed by a crash between the two writes is a valid, standalone record."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import replace

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config import secret_lifecycle
from blizzard.hub.domain.config.apply import (
    ConfigDeclaration,
    EntryOutcome,
    IConfigApplyWriter,
    StoredConfig,
    reconcile,
)
from blizzard.hub.domain.config.carry_over import (
    MIGRATION_CONTEXT,
    CommitCoordinate,
    ExistingConfig,
    IConfigImportWriter,
    IImportLinks,
    ImportResult,
    ImportStatus,
    LegacyImport,
    SealedSecretWrite,
    plan_import,
)
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.config.forge_resolution import (
    REPOSITORY_FORGE_RULES,
    WORK_SOURCE_FORGE_RULES,
    Authored,
    ForgeChoice,
    ForgeRules,
    ResolvedForge,
    decide_forge,
)
from blizzard.hub.domain.config.forges import (
    ConfiguredForge,
    ForgeEdit,
    ForgeFields,
    ForgeId,
    IWriteForgeRepository,
    forge_for_origin,
)
from blizzard.hub.domain.config.legacy_keys import LegacyKeys
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IWriteRepositoryRecordRepository,
    RepositoryDraft,
    RepositoryEdit,
    RepositoryFields,
)
from blizzard.hub.domain.config.secrets import (
    ISecretCipher,
    IWriteSecretRepository,
    SecretMetadata,
    SecretName,
    SecretValue,
)
from blizzard.hub.domain.config.work_sources import (
    ConfiguredWorkSource,
    IWriteWorkSourceRepository,
    WorkSourceDraft,
    WorkSourceEdit,
    WorkSourceFields,
    require_known_provider,
)
from blizzard.hub.domain.kernel.unset import UNSET, UnsetType


class ConfigAuthoring:
    def __init__(
        self,
        *,
        work_sources: IWriteWorkSourceRepository,
        repositories: IWriteRepositoryRecordRepository,
        forges: IWriteForgeRepository,
        secrets: IWriteSecretRepository,
        cipher: ISecretCipher,
        apply_writer: IConfigApplyWriter,
        import_writer: IConfigImportWriter,
        clock: IClock,
    ) -> None:
        self._work_sources = work_sources
        self._repositories = repositories
        self._forges = forges
        self._secrets = secrets
        self._cipher = cipher
        self._apply_writer = apply_writer
        self._import_writer = import_writer
        self._clock = clock

    # --- Forges ------------------------------------------------------------------

    def create_forge(
        self, slug: str, name: str | None, kind: str, fields: ForgeFields, ctx: ChangeContext
    ) -> ConfiguredForge:
        record, change = ConfiguredForge.new(slug, name, kind, fields, ctx, at=self._clock.now())
        return self._forges.create(record, change=change)

    def edit_forge(
        self, record: ConfiguredForge, edit: ForgeEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredForge:
        """Edit the forge; the addresses every record naming it holds move with it in the same write."""
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        return self._forges.update(
            edited, from_revision=record.revision, left_slug=record.slug_left_for(edited), change=change
        )

    def retire_forge(
        self, record: ConfiguredForge, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredForge:
        """Retire the forge. :class:`~blizzard.hub.domain.config.forges.ForgeInUse` while an enabled record names it."""
        return self._set_forge_retired(record, True, ctx, if_match)

    def enable_forge(
        self, record: ConfiguredForge, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredForge:
        return self._set_forge_retired(record, False, ctx, if_match)

    def _set_forge_retired(
        self, record: ConfiguredForge, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> ConfiguredForge:
        now = self._clock.now()
        decided = record.set_retired(retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        return self._forges.record_lifecycle(
            moved, retired=retired, from_revision=record.revision, at=now, by=ctx.actor, change=change
        )

    def resolve_forge(
        self,
        choice: ForgeChoice,
        rules: ForgeRules,
        ctx: ChangeContext,
        *,
        provider: str | None = None,
        current: ForgeId | None = None,
    ) -> ResolvedForge | None:
        """The forge ``choice`` names for a record of ``provider`` — one found, or one made for an origin no forge holds
        and committed on its own — with a warning per old field used. ``current`` is the forge an edited record names;
        ``None`` when an edit names nothing, leaving it as stored. The refusals of
        :func:`~blizzard.hub.domain.config.forge_resolution.decide_forge`."""
        named = self._forges.resolve(choice.forge) if choice.forge is not UNSET else None
        decision = decide_forge(
            choice,
            rules,
            provider=provider,
            current=None if current is None else self._forges.get(current),
            named=named,
            holder_of=self._forges.get_by_api_base,
        )
        if decision is None:
            return None
        forge = decision.forge
        if forge is None:
            assert decision.wanted is not None
            wanted = decision.wanted
            taken = {held.slug for held in self._forges.list_all(include_retired=True)}
            record, change = forge_for_origin(
                wanted.api_base, wanted.web_base, taken, ctx, at=self._clock.now(), kind=wanted.kind
            )
            forge = self._forges.find_or_create(record, change=change)
        return ResolvedForge(forge, decision.warnings(forge))

    # --- Work sources ------------------------------------------------------------

    def create_work_source_naming(
        self, slug: str, name: str | None, draft: WorkSourceDraft, choice: ForgeChoice, ctx: ChangeContext
    ) -> Authored[ConfiguredWorkSource]:
        """Create a work source on the forge ``choice`` names (:meth:`resolve_forge`)."""
        require_known_provider(draft.provider)
        resolved = self.resolve_forge(choice, WORK_SOURCE_FORGE_RULES, ctx, provider=draft.provider)
        assert resolved is not None
        record = self.create_work_source(slug, name, draft.on(resolved.forge.forge_id), ctx)
        return Authored(record, resolved.warnings)

    def edit_work_source_naming(
        self,
        record: ConfiguredWorkSource,
        edit: WorkSourceEdit,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        if_match: int | None = None,
    ) -> Authored[ConfiguredWorkSource]:
        """Edit a work source, re-pointing it at the forge ``choice`` names, if any (:meth:`resolve_forge`)."""
        record.require_revision(if_match)
        provider = record.fields.provider if isinstance(edit.provider, UnsetType) else edit.provider
        require_known_provider(provider)
        resolved = self.resolve_forge(
            choice, WORK_SOURCE_FORGE_RULES, ctx, provider=provider, current=record.fields.forge_id
        )
        if resolved is not None:
            edit = replace(edit, forge_id=resolved.forge.forge_id)
        edited = self.edit_work_source(record, edit, ctx, if_match=if_match)
        return Authored(edited, () if resolved is None else resolved.warnings)

    def create_work_source(
        self, slug: str, name: str | None, fields: WorkSourceFields, ctx: ChangeContext
    ) -> ConfiguredWorkSource:
        record, change = ConfiguredWorkSource.new(slug, name, fields, ctx, at=self._clock.now())
        return self._work_sources.create(record, change=change)

    def edit_work_source(
        self, record: ConfiguredWorkSource, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        return self._work_sources.update(
            edited, from_revision=record.revision, left_slug=record.slug_left_for(edited), change=change
        )

    def retire_work_source(
        self, record: ConfiguredWorkSource, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        return self._set_work_source_retired(record, True, ctx, if_match)

    def enable_work_source(
        self, record: ConfiguredWorkSource, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        return self._set_work_source_retired(record, False, ctx, if_match)

    def _set_work_source_retired(
        self, record: ConfiguredWorkSource, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> ConfiguredWorkSource:
        now = self._clock.now()
        decided = record.set_retired(retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        return self._work_sources.record_lifecycle(
            moved, retired=retired, from_revision=record.revision, at=now, by=ctx.actor, change=change
        )

    # --- Repositories ------------------------------------------------------------

    def create_repository_naming(
        self, slug: str, name: str | None, draft: RepositoryDraft, choice: ForgeChoice, ctx: ChangeContext
    ) -> Authored[ConfiguredRepository]:
        """Create a repository on the forge ``choice`` names (:meth:`resolve_forge`)."""
        resolved = self.resolve_forge(choice, REPOSITORY_FORGE_RULES, ctx)
        assert resolved is not None
        record = self.create_repository(slug, name, draft.on(resolved.forge.forge_id), ctx)
        return Authored(record, resolved.warnings)

    def edit_repository_naming(
        self,
        record: ConfiguredRepository,
        edit: RepositoryEdit,
        choice: ForgeChoice,
        ctx: ChangeContext,
        *,
        if_match: int | None = None,
    ) -> Authored[ConfiguredRepository]:
        """Edit a repository, re-pointing it at the forge ``choice`` names, if any (:meth:`resolve_forge`)."""
        record.require_revision(if_match)
        resolved = self.resolve_forge(choice, REPOSITORY_FORGE_RULES, ctx, current=record.fields.forge_id)
        if resolved is not None:
            edit = replace(edit, forge_id=resolved.forge.forge_id)
        edited = self.edit_repository(record, edit, ctx, if_match=if_match)
        return Authored(edited, () if resolved is None else resolved.warnings)

    def create_repository(
        self, slug: str, name: str | None, fields: RepositoryFields, ctx: ChangeContext
    ) -> ConfiguredRepository:
        record, change = ConfiguredRepository.new(slug, name, fields, ctx, at=self._clock.now())
        return self._repositories.create(record, change=change)

    def edit_repository(
        self, record: ConfiguredRepository, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        return self._repositories.update(
            edited, from_revision=record.revision, left_slug=record.slug_left_for(edited), change=change
        )

    def retire_repository(
        self, record: ConfiguredRepository, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        return self._set_repository_retired(record, True, ctx, if_match)

    def enable_repository(
        self, record: ConfiguredRepository, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        return self._set_repository_retired(record, False, ctx, if_match)

    def _set_repository_retired(
        self, record: ConfiguredRepository, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> ConfiguredRepository:
        now = self._clock.now()
        decided = record.set_retired(retired, ctx, if_match=if_match, at=now)
        if decided is None:
            return record
        moved, change = decided
        return self._repositories.record_lifecycle(
            moved, retired=retired, from_revision=record.revision, at=now, by=ctx.actor, change=change
        )

    # --- Apply -------------------------------------------------------------------

    def apply(
        self, declaration: ConfigDeclaration, stored: StoredConfig, ctx: ChangeContext, *, dry_run: bool
    ) -> tuple[EntryOutcome, ...]:
        """Reconcile ``declaration`` against ``stored`` and commit every write in one transaction, or
        — under ``dry_run`` — run that same transaction and roll it back. The outcomes are the same either way."""
        plan = reconcile(declaration, stored, ctx, at=self._clock.now())
        if plan.writes:
            self._apply_writer.apply(plan.writes, dry_run=dry_run)
        return plan.outcomes

    # --- Carry-over --------------------------------------------------------------

    def import_legacy(
        self,
        legacy: LegacyKeys,
        values: Mapping[str, str],
        commits: Sequence[CommitCoordinate],
        existing: ExistingConfig,
        *,
        recorded: bool,
        links: IImportLinks,
    ) -> ImportResult:
        """Carry ``legacy`` into records once, sealing each secret's value before the one write, and link each
        work source and repository it creates through ``links``. ``values`` holds the legacy variables' values;
        nothing is written when an import was already recorded or no legacy key is present."""
        if recorded:
            return ImportResult(ImportStatus.ALREADY_IMPORTED)
        if not legacy.present():
            return ImportResult(ImportStatus.NOTHING_TO_IMPORT)
        at = self._clock.now()
        plan = plan_import(legacy, values, commits, existing, ctx=MIGRATION_CONTEXT, at=at)
        created = (
            [source.work_source_id for source, _ in plan.work_sources],
            [repo.repository_id for repo, _ in plan.repositories],
        )
        linked = links.links(*created, MIGRATION_CONTEXT, at=at) if any(created) else ()
        sealed = tuple(
            SealedSecretWrite(
                name=secret.name.value,
                sealed=self._cipher.seal(
                    SecretValue.entered(values[secret.variable]), name=secret.name.value, revision=1
                ),
                change=secret.change,
            )
            for secret in plan.secrets
        )
        imported = LegacyImport(
            secrets=sealed,
            forges=plan.forges,
            work_sources=plan.work_sources,
            repositories=plan.repositories,
            fact=plan.fact,
            links=linked,
        )
        if not self._import_writer.write(imported):
            return ImportResult(ImportStatus.ALREADY_IMPORTED)
        return ImportResult(ImportStatus.IMPORTED, plan.outcomes)

    # --- Secrets -----------------------------------------------------------------

    def create_secret(self, name: SecretName, value: str, ctx: ChangeContext) -> SecretMetadata:
        sealed = self._cipher.seal(SecretValue.entered(value), name=name.value, revision=1)
        now = self._clock.now()
        change = secret_lifecycle.creation(name, ctx, at=now)
        return self._secrets.create(name.value, sealed=sealed, at=now, by=ctx.actor, change=change)

    def replace_secret(
        self, record: SecretMetadata, value: str, ctx: ChangeContext, *, if_match: int | None = None
    ) -> SecretMetadata:
        """Seal under the next revision and compare-and-set from ``record.revision``.
        ``if_match`` is the revision the caller last saw, checked before any write."""
        now = self._clock.now()
        change = secret_lifecycle.replacement(record, ctx, retired=record.retired, if_match=if_match, at=now)
        sealed = self._cipher.seal(SecretValue.entered(value), name=record.name, revision=change.revision)
        return self._secrets.replace(
            record.name, from_revision=record.revision, sealed=sealed, at=now, by=ctx.actor, change=change
        )

    def retire_secret(self, record: SecretMetadata, ctx: ChangeContext) -> bool:
        """Retire the secret; ``False`` when it already was. :class:`SecretReferenced` when an
        active record names it."""
        return self._set_secret_retired(record, True, ctx)

    def enable_secret(self, record: SecretMetadata, ctx: ChangeContext) -> bool:
        """Re-enable the secret; ``False`` when it already was active."""
        return self._set_secret_retired(record, False, ctx)

    def _set_secret_retired(self, record: SecretMetadata, retired: bool, ctx: ChangeContext) -> bool:
        now = self._clock.now()
        change = secret_lifecycle.lifecycle_change(record, ctx, retired_now=record.retired, retired=retired, at=now)
        if change is None:
            return False
        self._secrets.record_lifecycle(record.name, retired=retired, at=now, by=ctx.actor, change=change)
        return True
