"""SQLAlchemy adapter for the declarative apply (package-private).

One ``store.write`` executes every planned write through the same per-operation bodies the per-record
adapters run, so each write keeps its in-transaction checks — secret availability, name and locator or
coordinate uniqueness, the compare-and-set on ``from_revision`` — and the first refusal rolls the whole
apply back. A dry run is that transaction with a rollback after the last write (``bzh:config-apply``)."""

from __future__ import annotations

from collections.abc import Sequence

from sqlalchemy.engine import Connection
from sqlalchemy.exc import IntegrityError

from blizzard.hub.domain.config.apply import (
    ApplyEntryRefused,
    IConfigApplyWriter,
    PlannedWrite,
)
from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange
from blizzard.hub.domain.config.repositories import (
    ConfiguredRepository,
    RepositoryCoordinateTaken,
    RepositoryNameTaken,
)
from blizzard.hub.domain.config.work_sources import (
    ConfigFieldError,
    ConfigRevisionConflict,
    ConfiguredWorkSource,
    WorkSourceLocatorTaken,
    WorkSourceNameTaken,
)
from blizzard.hub.domain.garden.routines import SCOPES_FIELD, Routine, RoutineNameTakenError
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlugTakenError
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal import repository_record_store as repository_writes
from blizzard.hub.store.internal import routine_store as routine_writes
from blizzard.hub.store.internal import scope_store as scope_writes
from blizzard.hub.store.internal import work_source_record_store as work_source_writes

#: What a write's in-transaction checks raise; each is located at its document entry.
_REFUSALS = (
    ConfigFieldError,
    ConfigRevisionConflict,
    WorkSourceNameTaken,
    WorkSourceLocatorTaken,
    RepositoryNameTaken,
    RepositoryCoordinateTaken,
    RoutineNameTakenError,
    ScopeSlugTakenError,
)


def _taken(record: object, name: str) -> Exception:
    """The refusal a create that lost its name to a concurrent one reads as."""
    if isinstance(record, ConfiguredWorkSource):
        return WorkSourceNameTaken(name)
    if isinstance(record, Routine):
        return RoutineNameTakenError(name)
    if isinstance(record, Scope):
        return ScopeSlugTakenError(name)
    return RepositoryNameTaken(name)


class _DryRunRollback(Exception):
    """Raised after the last write of a dry run so ``write`` rolls the transaction back."""


class ConfigApplyStore:
    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def apply(self, writes: Sequence[PlannedWrite], *, dry_run: bool) -> None:
        try:
            with self._store.write("apply", expect=(ApplyEntryRefused, _DryRunRollback)) as conn:
                for write in writes:
                    try:
                        self._run(conn, write)
                    except IntegrityError as exc:  # a concurrent create won the race
                        cause = _taken(write.record, write.record.name)
                        raise ApplyEntryRefused(write.section, write.index, cause) from exc
                    except _REFUSALS as exc:
                        raise ApplyEntryRefused(write.section, write.index, exc) from exc
                if dry_run:
                    raise _DryRunRollback
        except _DryRunRollback:
            return

    @staticmethod
    def _run(conn: Connection, write: PlannedWrite) -> None:
        record, change, from_revision = write.record, write.change, write.from_revision
        lifecycle = change.op in (ChangeOp.RETIRE, ChangeOp.ENABLE)
        retired = change.op is ChangeOp.RETIRE
        if isinstance(record, Scope):
            _run_scope(conn, record, from_revision, change, lifecycle=lifecycle, retired=retired)
        elif isinstance(record, Routine):
            _run_routine(conn, record, from_revision, change, lifecycle=lifecycle, retired=retired)
        elif isinstance(record, ConfiguredWorkSource):
            if from_revision is None:
                work_source_writes.insert_record(conn, record, change)
            elif lifecycle:
                work_source_writes.record_lifecycle_fact(
                    conn,
                    record,
                    retired=retired,
                    from_revision=from_revision,
                    at=change.recorded_at,
                    by=change.actor,
                    change=change,
                )
            else:
                work_source_writes.update_record(conn, record, from_revision, change)
        else:
            assert isinstance(record, ConfiguredRepository)
            ConfigApplyStore._run_repository(conn, record, from_revision, change, lifecycle=lifecycle, retired=retired)

    @staticmethod
    def _run_repository(
        conn: Connection,
        record: ConfiguredRepository,
        from_revision: int | None,
        change: ConfigChange,
        *,
        lifecycle: bool,
        retired: bool,
    ) -> None:
        if from_revision is None:
            repository_writes.insert_record(conn, record, change)
        elif lifecycle:
            repository_writes.record_lifecycle_fact(
                conn,
                record,
                retired=retired,
                from_revision=from_revision,
                at=change.recorded_at,
                by=change.actor,
                change=change,
            )
        else:
            repository_writes.update_record(conn, record, from_revision, change)


def _run_scope(
    conn: Connection, record: Scope, from_revision: int | None, change: ConfigChange, *, lifecycle: bool, retired: bool
) -> None:
    if from_revision is None:
        if not scope_writes.ensure_scope(conn, (record, change)):
            raise ScopeSlugTakenError(record.slug)
    elif lifecycle:
        scope_writes.record_scope_lifecycle(
            conn,
            record,
            retired=retired,
            from_revision=from_revision,
            at=change.recorded_at,
            by=change.actor,
            change=change,
        )
    else:
        scope_writes.update_scope(conn, record, from_revision, change)


def _run_routine(
    conn: Connection,
    record: Routine,
    from_revision: int | None,
    change: ConfigChange,
    *,
    lifecycle: bool,
    retired: bool,
) -> None:
    if from_revision is None:
        routine_writes.insert_routine(conn, record, change)
    elif lifecycle:
        routine_writes.record_routine_lifecycle(
            conn,
            record,
            retired=retired,
            from_revision=from_revision,
            at=change.recorded_at,
            by=change.actor,
            change=change,
        )
    else:
        linked = next((d.new for d in change.diff if d.field == SCOPES_FIELD), None)
        if linked is None:
            routine_writes.update_routine(conn, record, from_revision, change)
        else:
            assert isinstance(linked, list)
            routine_writes.set_linked_scopes(conn, record, linked, from_revision=from_revision, change=change)


def _conforms_config_apply_store(x: ConfigApplyStore) -> IConfigApplyWriter:
    return x
