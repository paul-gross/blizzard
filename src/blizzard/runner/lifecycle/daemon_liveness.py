"""The daemon-liveness repository seam: when this host's runner daemon was last known alive.

Host-scoped — the beat belongs to the process, not to the runner it is bound to, so it is reached through
:class:`~blizzard.runner.stores.HostStores`."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

__all__ = ["IReadDaemonLivenessRepository", "IWriteDaemonLivenessRepository"]


class IReadDaemonLivenessRepository(Protocol):
    """Read-only daemon-liveness query."""

    def last_daemon_liveness(self) -> datetime | None:
        """When the runner was last known alive, or ``None`` if it never ticked.

        Stamped once per liveness beat, so after a crash the newest value is when the daemon
        died, to within one beat."""
        ...


class IWriteDaemonLivenessRepository(IReadDaemonLivenessRepository, Protocol):
    """Read-write daemon-liveness store."""

    def record_daemon_liveness(self, *, alive_at: datetime) -> None:
        """Stamp the runner as alive at ``alive_at`` — the tick's liveness beat.

        Upserted, one row: only the newest instant matters, and it is the crash-time
        reference startup recovery reads back via :meth:`last_daemon_liveness`."""
        ...
