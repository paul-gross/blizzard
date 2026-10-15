"""Moving one runner's rows between two runner stores — the seams ``runner ingest`` and ``runner store reset --runner``
stand on, in opposite directions.

A copy writes one runner's rows into a destination store in one transaction, the destination issuing every integer
surrogate key anew, with the source's host rows merged by each host table's declared treatment and any ingest recorded
there too; it never writes the source. A removal deletes one runner's rows from one store."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model

__all__ = [
    "DanglingReference",
    "HostIngest",
    "IReadHostIngestRepository",
    "IRunnerRowRemoval",
    "IRunnerTransfer",
    "IRunnerTransferHandle",
    "IWriteHostIngestRepository",
    "WorkspacePromptConflict",
]


@domain_model
@dataclass(frozen=True)
class HostIngest:
    """One runner this host took in: from which resolved ``source``, the source's last liveness stamp at the copy
    (``None`` for a source that never ticked), and when, by this host's clock."""

    runner_id: str
    source: str
    source_alive_at: datetime | None
    ingested_at: datetime


class WorkspacePromptConflict(RuntimeError):
    """Both stores hold a different prompt override for one workspace: neither may silently win."""

    def __init__(self, workspace_id: str, held: str, offered: str) -> None:
        super().__init__(
            f"workspace {workspace_id} already has a prompt override here ({held!r}) and the source holds a "
            f"different one ({offered!r}); clear one of them first"
        )
        self.workspace_id = workspace_id
        self.held = held
        self.offered = offered


class DanglingReference(RuntimeError):
    """A required reference names a row the source does not hold, so no key in the destination can stand for it."""

    def __init__(self, column: str, value: int) -> None:
        super().__init__(f"{column} = {value} names a row the source does not hold")
        self.column = column
        self.value = value


class IRunnerTransferHandle(Protocol):
    """One open copy — a read of the source and a write transaction on the destination. The domain-facing view
    declares no connection access; each store's own ``*_locked`` method recovers what it runs on."""

    @property
    def runner_id(self) -> str:
        """The runner the copy moves."""
        ...


class IReadHostIngestRepository(Protocol):
    """Read the ingests this host has recorded."""

    def latest_host_ingest(self, runner_id: str) -> HostIngest | None:
        """The newest ingest recorded for ``runner_id``, or ``None`` if this host never ingested it."""
        ...


class IWriteHostIngestRepository(IReadHostIngestRepository, Protocol):
    """The host half of a copy: its host-row merge and its ingest record, each on the copy's own transaction."""

    def merge_host_rows_locked(self, handle: IRunnerTransferHandle, *, workspaces: Mapping[str, str]) -> None:
        """Apply each host-scoped table's declared treatment to the source's host rows on ``handle``'s transaction.
        ``workspaces`` maps each workspace id the moving runner's rows name to the id it takes in the destination.
        Raises :class:`WorkspacePromptConflict` when both stores override one workspace's prompt differently."""
        ...

    def record_host_ingest_locked(self, handle: IRunnerTransferHandle, ingest: HostIngest) -> None:
        """Append ``ingest`` on ``handle``'s transaction."""
        ...


class IRunnerTransfer(Protocol):
    """Copy one runner from a source store into a destination store."""

    def copy_runner(self, *, workspace_ids: Mapping[str, str] | None = None, ingest: HostIngest | None = None) -> None:
        """Copy every runner-scoped row of the runner, its outbox rows under their own ``seq``s and each lane's counter
        at the larger of the two stores', then merge the source's host rows and record ``ingest`` when given — all in
        one destination transaction, so a failure leaves the destination as it was. ``workspace_ids`` renames a
        workspace id the copied rows name; an id it leaves out keeps its name."""
        ...


class IRunnerRowRemoval(Protocol):
    """Delete one runner's rows from one store."""

    def remove_runner_rows(self, *, keep_counters: bool) -> None:
        """Delete every runner-scoped row of the runner, its identity included, in one transaction; host rows stay.
        ``keep_counters`` leaves its outbox counters, so a runner that runs here again never issues a ``seq`` at or
        below one its hub already holds."""
        ...
