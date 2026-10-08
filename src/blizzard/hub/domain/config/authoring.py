"""``ConfigAuthoring`` — the one writer of configured records.

Every verb takes the record the edge resolved (``bzh:domain-takes-objects``), asks the record's model for the change,
and hands it to the write repository with the record write. A write that changes nothing writes nothing. Secrets
route through here too."""

from __future__ import annotations

from collections.abc import Mapping, Sequence

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
    ImportResult,
    ImportStatus,
    LegacyImport,
    SealedSecretWrite,
    plan_import,
)
from blizzard.hub.domain.config.changes import ChangeContext
from blizzard.hub.domain.config.legacy_keys import LegacyKeys
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    IWriteRepositoryRecordRepository,
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
    WorkSourceEdit,
    WorkSourceFields,
)


class ConfigAuthoring:
    def __init__(
        self,
        *,
        work_sources: IWriteWorkSourceRepository,
        repositories: IWriteRepositoryRecordRepository,
        secrets: IWriteSecretRepository,
        cipher: ISecretCipher,
        apply_writer: IConfigApplyWriter,
        import_writer: IConfigImportWriter,
        clock: IClock,
    ) -> None:
        self._work_sources = work_sources
        self._repositories = repositories
        self._secrets = secrets
        self._cipher = cipher
        self._apply_writer = apply_writer
        self._import_writer = import_writer
        self._clock = clock

    # --- Work sources ------------------------------------------------------------

    def create_work_source(self, name: str, fields: WorkSourceFields, ctx: ChangeContext) -> ConfiguredWorkSource:
        record, change = ConfiguredWorkSource.new(name, fields, ctx, at=self._clock.now())
        return self._work_sources.create(record, change=change)

    def edit_work_source(
        self, record: ConfiguredWorkSource, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredWorkSource:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        return self._work_sources.update(edited, from_revision=record.revision, change=change)

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

    def create_repository(self, name: str, fields: RepositoryFields, ctx: ChangeContext) -> ConfiguredRepository:
        record, change = ConfiguredRepository.new(name, fields, ctx, at=self._clock.now())
        return self._repositories.create(record, change=change)

    def edit_repository(
        self, record: ConfiguredRepository, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> ConfiguredRepository:
        decided = record.edit(edit, ctx, if_match=if_match, at=self._clock.now())
        if decided is None:
            return record
        edited, change = decided
        return self._repositories.update(edited, from_revision=record.revision, change=change)

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
    ) -> ImportResult:
        """Carry ``legacy`` into records once, sealing each secret's value before the one write.
        ``values`` holds the legacy variables' values; nothing is written when an import was
        already recorded or no legacy key is present."""
        if recorded:
            return ImportResult(ImportStatus.ALREADY_IMPORTED)
        if not legacy.present():
            return ImportResult(ImportStatus.NOTHING_TO_IMPORT)
        plan = plan_import(legacy, values, commits, existing, ctx=MIGRATION_CONTEXT, at=self._clock.now())
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
            secrets=sealed, work_sources=plan.work_sources, repositories=plan.repositories, fact=plan.fact
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
        retired = self._secrets.is_retired(record.name)
        change = secret_lifecycle.replacement(record, ctx, retired=retired, if_match=if_match, at=now)
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
        retired_now = self._secrets.is_retired(record.name)
        change = secret_lifecycle.lifecycle_change(record, ctx, retired_now=retired_now, retired=retired, at=now)
        if change is None:
            return False
        self._secrets.record_lifecycle(record.name, retired=retired, at=now, by=ctx.actor, change=change)
        return True
