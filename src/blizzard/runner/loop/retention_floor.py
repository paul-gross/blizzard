"""The cross-tick floor that gates the retention pass."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime, timedelta

from blizzard.foundation.roles import domain_model

#: How often the retention pass may run.
RETENTION_FLOOR = timedelta(hours=1)


@domain_model
@dataclass
class RetentionPasses:
    """When the last retention pass ran, held **across ticks** in memory — composition-root-owned
    and as long-lived as the loop itself, like
    :class:`~blizzard.runner.harness.capability_snapshot.HarnessVersionCache`."""

    floor: timedelta = RETENTION_FLOOR
    _last_pass: datetime | None = None

    def due(self, now: datetime) -> bool:
        """No pass has run yet, or the floor has elapsed since the last."""
        return self._last_pass is None or now - self._last_pass >= self.floor

    def record(self, now: datetime) -> None:
        self._last_pass = now
