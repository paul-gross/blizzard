"""``ConfigAuthoring`` — the one writer of configured records.

Every verb loads nothing: it takes the record the edge resolved (``bzh:domain-takes-objects``),
decides the change, and hands the write repository the record write together with the
:class:`ConfigChange` it must commit in the same transaction (``bzh:configured-record``).
A write that changes nothing writes nothing: no revision moves and no row is appended.

Secrets route through here too. A secret's retire and enable append their change row at the
secret's unchanged revision: its value is sealed against ``(name, revision)``, so moving the
revision without resealing would leave the value undecryptable."""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.config.changes import (
    ChangeContext,
    ChangeOp,
    ConfigChange,
    FieldChange,
    RecordKind,
)
from blizzard.hub.domain.config.repositories import (
    IWriteRepositoryRecordRepository,
    RepositoryEdit,
    RepositoryFields,
    RepositoryRecord,
)
from blizzard.hub.domain.config.repositories import diff as repository_diff
from blizzard.hub.domain.config.repositories import merge as repository_merge
from blizzard.hub.domain.config.repositories import validate_fields as validate_repository_fields
from blizzard.hub.domain.config.repositories import validate_name as validate_repository_name
from blizzard.hub.domain.config.work_sources import (
    ConfigRevisionConflict,
    IWriteWorkSourceRepository,
    WorkSourceEdit,
    WorkSourceFields,
    WorkSourceRecord,
    diff,
    merge,
    validate_fields,
    validate_name,
)
from blizzard.hub.domain.secrets import (
    ISecretCipher,
    IWriteSecretRepository,
    SecretName,
    SecretRecord,
    SecretRetired,
    SecretRevisionConflict,
    SecretValue,
)

_RETIRED = "retired"


class ConfigAuthoring:
    def __init__(
        self,
        *,
        work_sources: IWriteWorkSourceRepository,
        repositories: IWriteRepositoryRecordRepository,
        secrets: IWriteSecretRepository,
        cipher: ISecretCipher,
        clock: IClock,
    ) -> None:
        self._work_sources = work_sources
        self._repositories = repositories
        self._secrets = secrets
        self._cipher = cipher
        self._clock = clock

    # --- Work sources ------------------------------------------------------------

    def create_work_source(self, name: str, fields: WorkSourceFields, ctx: ChangeContext) -> WorkSourceRecord:
        validate_name(name)
        validate_fields(fields)
        now = self._clock.now()
        record = WorkSourceRecord(name=name, fields=fields, revision=1, created_at=now, created_by=ctx.actor)
        change = self._change(ctx, RecordKind.WORK_SOURCE, name, 1, ChangeOp.CREATE, diff(None, fields), now)
        return self._work_sources.create(record, change=change)

    def edit_work_source(
        self, record: WorkSourceRecord, edit: WorkSourceEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> WorkSourceRecord:
        """Apply a sparse edit. A retired source is edited too — the revision moves, the
        fact does not — but a retired secret is refused, as a write that enables would be."""
        self._check_match(record, if_match)
        merged = merge(record.fields, edit)
        changes = diff(record.fields, merged)
        if not changes:
            return record
        validate_fields(merged)
        now = self._clock.now()
        edited = replace(record, fields=merged, revision=record.revision + 1)
        change = self._change(ctx, RecordKind.WORK_SOURCE, record.name, edited.revision, ChangeOp.EDIT, changes, now)
        return self._work_sources.update(edited, from_revision=record.revision, change=change)

    def retire_work_source(
        self, record: WorkSourceRecord, ctx: ChangeContext, *, if_match: int | None = None
    ) -> WorkSourceRecord:
        return self._set_work_source_retired(record, True, ctx, if_match)

    def enable_work_source(
        self, record: WorkSourceRecord, ctx: ChangeContext, *, if_match: int | None = None
    ) -> WorkSourceRecord:
        return self._set_work_source_retired(record, False, ctx, if_match)

    def _set_work_source_retired(
        self, record: WorkSourceRecord, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> WorkSourceRecord:
        self._check_match(record, if_match)
        if record.retired == retired:
            return record
        now = self._clock.now()
        moved = replace(record, revision=record.revision + 1, retired=retired)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        change = self._change(
            ctx,
            RecordKind.WORK_SOURCE,
            record.name,
            moved.revision,
            op,
            (FieldChange(_RETIRED, record.retired, retired),),
            now,
        )
        return self._work_sources.record_lifecycle(
            moved, retired=retired, from_revision=record.revision, at=now, by=ctx.actor, change=change
        )

    @staticmethod
    def _check_match(record: WorkSourceRecord | RepositoryRecord, if_match: int | None) -> None:
        if if_match is not None and if_match != record.revision:
            kind = "repository" if isinstance(record, RepositoryRecord) else "work source"
            raise ConfigRevisionConflict(kind, record.name, current=record.revision)

    # --- Repositories ------------------------------------------------------------

    def create_repository(self, name: str, fields: RepositoryFields, ctx: ChangeContext) -> RepositoryRecord:
        validate_repository_name(name)
        validate_repository_fields(fields)
        now = self._clock.now()
        record = RepositoryRecord(name=name, fields=fields, revision=1, created_at=now, created_by=ctx.actor)
        change = self._change(ctx, RecordKind.REPOSITORY, name, 1, ChangeOp.CREATE, repository_diff(None, fields), now)
        return self._repositories.create(record, change=change)

    def edit_repository(
        self, record: RepositoryRecord, edit: RepositoryEdit, ctx: ChangeContext, *, if_match: int | None = None
    ) -> RepositoryRecord:
        """Apply a sparse edit. A retired repository is edited too — the revision moves, the
        fact does not — but a retired secret is refused, as a write that enables would be."""
        self._check_match(record, if_match)
        merged = repository_merge(record.fields, edit)
        changes = repository_diff(record.fields, merged)
        if not changes:
            return record
        validate_repository_fields(merged)
        now = self._clock.now()
        edited = replace(record, fields=merged, revision=record.revision + 1)
        change = self._change(ctx, RecordKind.REPOSITORY, record.name, edited.revision, ChangeOp.EDIT, changes, now)
        return self._repositories.update(edited, from_revision=record.revision, change=change)

    def retire_repository(
        self, record: RepositoryRecord, ctx: ChangeContext, *, if_match: int | None = None
    ) -> RepositoryRecord:
        return self._set_repository_retired(record, True, ctx, if_match)

    def enable_repository(
        self, record: RepositoryRecord, ctx: ChangeContext, *, if_match: int | None = None
    ) -> RepositoryRecord:
        return self._set_repository_retired(record, False, ctx, if_match)

    def _set_repository_retired(
        self, record: RepositoryRecord, retired: bool, ctx: ChangeContext, if_match: int | None
    ) -> RepositoryRecord:
        self._check_match(record, if_match)
        if record.retired == retired:
            return record
        now = self._clock.now()
        moved = replace(record, revision=record.revision + 1, retired=retired)
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        change = self._change(
            ctx,
            RecordKind.REPOSITORY,
            record.name,
            moved.revision,
            op,
            (FieldChange(_RETIRED, record.retired, retired),),
            now,
        )
        return self._repositories.record_lifecycle(
            moved, retired=retired, from_revision=record.revision, at=now, by=ctx.actor, change=change
        )

    # --- Secrets -----------------------------------------------------------------

    def create_secret(self, name: SecretName, value: str, ctx: ChangeContext) -> SecretRecord:
        sealed = self._cipher.seal(SecretValue(value), name=name.value, revision=1)
        now = self._clock.now()
        change = self._change(ctx, RecordKind.SECRET, name.value, 1, ChangeOp.CREATE, (), now)
        return self._secrets.create(name.value, sealed=sealed, at=now, by=ctx.actor, change=change)

    def replace_secret(
        self, record: SecretRecord, value: str, ctx: ChangeContext, *, if_match: int | None = None
    ) -> SecretRecord:
        """Seal under ``record.revision + 1`` and compare-and-set from ``record.revision``.
        ``if_match`` is the revision the caller last saw, checked before any write."""
        if self._secrets.is_retired(record.name):
            raise SecretRetired(record.name)
        if if_match is not None and if_match != record.revision:
            raise SecretRevisionConflict(record.name, current=record.revision)
        sealed = self._cipher.seal(SecretValue(value), name=record.name, revision=record.revision + 1)
        now = self._clock.now()
        change = self._change(ctx, RecordKind.SECRET, record.name, record.revision + 1, ChangeOp.REPLACE, (), now)
        return self._secrets.replace(
            record.name, from_revision=record.revision, sealed=sealed, at=now, by=ctx.actor, change=change
        )

    def retire_secret(self, record: SecretRecord, ctx: ChangeContext) -> bool:
        """Retire the secret; ``False`` when it already was. :class:`SecretReferenced` when an
        active record names it."""
        return self._set_secret_retired(record, True, ctx)

    def enable_secret(self, record: SecretRecord, ctx: ChangeContext) -> bool:
        """Re-enable the secret; ``False`` when it already was active."""
        return self._set_secret_retired(record, False, ctx)

    def _set_secret_retired(self, record: SecretRecord, retired: bool, ctx: ChangeContext) -> bool:
        if self._secrets.is_retired(record.name) == retired:
            return False
        now = self._clock.now()
        op = ChangeOp.RETIRE if retired else ChangeOp.ENABLE
        change = self._change(
            ctx,
            RecordKind.SECRET,
            record.name,
            record.revision,
            op,
            (FieldChange(_RETIRED, not retired, retired),),
            now,
        )
        self._secrets.record_lifecycle(record.name, retired=retired, at=now, by=ctx.actor, change=change)
        return True

    @staticmethod
    def _change(
        ctx: ChangeContext,
        kind: RecordKind,
        key: str,
        revision: int,
        op: ChangeOp,
        changes: tuple[FieldChange, ...],
        at: datetime,
    ) -> ConfigChange:
        return ConfigChange(
            recorded_at=at,
            actor=ctx.actor,
            door=ctx.door,
            record_kind=kind,
            record_key=key,
            revision=revision,
            op=op,
            diff=changes,
        )
