"""Names the unit and component halves of the work-item closure tests share."""

from __future__ import annotations

from datetime import UTC, datetime

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.work_items.closure import CloseDrainSweep, CloseIntentDrainer, IReadCloseIntentCorpus
from blizzard.hub.tenancy import TenantId

NOW = datetime(2026, 8, 1, tzinfo=UTC)


FAKE_TENANT = TenantId("ten_close_drain")


def sweep_over(corpus: IReadCloseIntentCorpus, drainer: CloseIntentDrainer, clock: IClock) -> CloseDrainSweep:
    """The hub's drain pass over ``corpus``, handing every tenant's due intents to ``drainer``."""
    return CloseDrainSweep(corpus=corpus, tenant=lambda _tenant: drainer, clock=clock)
