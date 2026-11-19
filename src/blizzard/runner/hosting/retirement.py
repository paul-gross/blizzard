"""A retirement the running host notices at its hub, recorded as the runner's ``retired`` fact."""

from __future__ import annotations

import threading
from dataclasses import dataclass, field

from blizzard.foundation.clock import IClock
from blizzard.runner.hosting.model import HUB, HostingFact
from blizzard.runner.hosting.ports import IWriteHostedRunnerRepository
from blizzard.runner.hub.identity import IRecordRunnerRetired


class RetirementNotices:
    """Whether a retirement was recorded since the host tick last asked — so the tick drains it in the same pass."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._noticed = False

    def notice(self) -> None:
        with self._lock:
            self._noticed = True

    def take(self) -> bool:
        """Whether one was noticed since the last take; clears the notice."""
        with self._lock:
            noticed, self._noticed = self._noticed, False
        return noticed


@dataclass(frozen=True)
class HubNoticedRetirement:
    """Records ``runner_id``'s ``retired`` fact under the ``hub`` door and actor, stamped by ``clock``. A runner the
    store does not host yet is hosted in the same transaction, so its retirement has a row to belong to."""

    runner_id: str
    hosted: IWriteHostedRunnerRepository
    clock: IClock
    notices: RetirementNotices = field(default_factory=RetirementNotices)

    def record_retired(self) -> bool:
        if not self.hosted.record_retired(self.runner_id, HostingFact(actor=HUB, door=HUB, at=self.clock.now())):
            return False
        self.notices.notice()
        return True


_record_runner_retired_conforms: type[IRecordRunnerRetired] = HubNoticedRetirement
