"""SQLAlchemy adapter for the declarative apply (package-private).

One ``store.write`` executes every planned write through the same per-operation bodies the per-record
adapters run, and the first refusal rolls the whole apply back. A dry run is that transaction with a
rollback after the last write (``bzh:config-apply``)."""

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
    RepositorySlugTaken,
)
from blizzard.hub.domain.config.work_sources import (
    ConfigFieldError,
    ConfigRevisionConflict,
    ConfiguredWorkSource,
    WorkSourceLocatorTaken,
    WorkSourceSlugTaken,
)
from blizzard.hub.domain.garden.routines import SCOPES_FIELD, Routine, RoutineNameTakenError
from blizzard.hub.domain.garden.scopes import Scope, ScopeSlugTakenError
from blizzard.hub.domain.projects.links import RepositoryLink, SourceLink
from blizzard.hub.domain.projects.model import Project, ProjectSlugTaken
from blizzard.hub.store.internal import project_link_store as link_writes
from blizzard.hub.store.internal import project_store as project_writes
from blizzard.hub.store.internal import repository_record_store as repository_writes
from blizzard.hub.store.internal import routine_store as routine_writes
from blizzard.hub.store.internal import scope_store as scope_writes
from blizzard.hub.store.internal import work_source_record_store as work_source_writes
from blizzard.hub.store.scoped import ScopedConnections

#: What a write's in-transaction checks raise; each is located at its document entry.
_REFUSALS = (
    ConfigFieldError,
    ConfigRevisionConflict,
    WorkSourceSlugTaken,
    WorkSourceLocatorTaken,
    RepositorySlugTaken,
    RepositoryCoordinateTaken,
    RoutineNameTakenError,
    ScopeSlugTakenError,
    ProjectSlugTaken,
)


def _taken(record: object) -> Exception:
    """The refusal a create that lost its name to a concurrent one reads as."""
    if isinstance(record, ConfiguredWorkSource):
        return WorkSourceSlugTaken(record.slug)
    if isinstance(record, Routine):
        return RoutineNameTakenError(record.name)
    if isinstance(record, Scope):
        return ScopeSlugTakenError(record.name)
    if isinstance(record, Project):
        return ProjectSlugTaken(record.slug)
    if isinstance(record, SourceLink | RepositoryLink):
        return ConfigRevisionConflict(record.LABEL, f"{record.project_id}/{record.target()}", current=record.revision)
    assert isinstance(record, ConfiguredRepository)
    return RepositorySlugTaken(record.slug)


class _DryRunRollback(Exception):
    """Raised after the last write of a dry run so ``write`` rolls the transaction back."""


class ConfigApplyStore:
    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def apply(self, writes: Sequence[PlannedWrite], *, dry_run: bool) -> None:
        try:
            with self._store.write("apply", expect=(ApplyEntryRefused, _DryRunRollback)) as conn:
                for write in writes:
                    try:
                        self._run(conn, write)
                    except IntegrityError as exc:  # a concurrent create won the race
                        cause = _taken(write.record)
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
        elif isinstance(record, Project | SourceLink | RepositoryLink):
            _run_project(conn, record, from_revision, change)
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
                work_source_writes.update_record(conn, record, from_revision, None, change)
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
            repository_writes.update_record(conn, record, from_revision, None, change)


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


def _run_project(
    conn: Connection, record: Project | SourceLink | RepositoryLink, from_revision: int | None, change: ConfigChange
) -> None:
    """A project is created or edited — an apply never moves its slug —, and a link created, unlinked or relinked."""
    if isinstance(record, Project):
        if from_revision is None:
            project_writes.insert_project(conn, record, change)
        else:
            project_writes.update_project(conn, record, from_revision, None, change)
        return
    kind = link_writes._SOURCES if isinstance(record, SourceLink) else link_writes._REPOSITORIES
    if from_revision is None:
        link_writes.insert_link(conn, kind, record.project_id, record.target(), change)
    else:
        link_writes.record_link(
            conn,
            kind,
            record.project_id,
            record.target(),
            linked=record.linked,
            revision=record.revision,
            from_revision=from_revision,
            change=change,
        )


def _conforms_config_apply_store(x: ConfigApplyStore) -> IConfigApplyWriter:
    return x
