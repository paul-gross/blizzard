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
from blizzard.hub.domain.config.changes import ChangeOp
from blizzard.hub.domain.config.repositories import (
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
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal import repository_record_store as repository_writes
from blizzard.hub.store.internal import work_source_record_store as work_source_writes

#: What a write's in-transaction checks raise; each is located at its document entry.
_REFUSALS = (
    ConfigFieldError,
    ConfigRevisionConflict,
    WorkSourceNameTaken,
    WorkSourceLocatorTaken,
    RepositoryNameTaken,
    RepositoryCoordinateTaken,
)


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
                        taken = (
                            WorkSourceNameTaken
                            if isinstance(write.record, ConfiguredWorkSource)
                            else RepositoryNameTaken
                        )
                        raise ApplyEntryRefused(write.section, write.index, taken(write.record.name)) from exc
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
        if isinstance(record, ConfiguredWorkSource):
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
        elif from_revision is None:
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


def _conforms_config_apply_store(x: ConfigApplyStore) -> IConfigApplyWriter:
    return x
