"""A delivery plan written through its domain composition over the real hub repositories, for a test driving a
garden or review delivery's writes directly in a unit it opened itself."""

from __future__ import annotations

from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import DeliveryOutcome, DeliveryPlan, write_delivery_plan
from blizzard.hub.domain.garden.review.materialize import (
    ReviewFindingsOutcome,
    ReviewFindingsPlan,
    write_review_plan,
)
from blizzard.hub.domain.kernel.transactions import LockedTransaction
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.review_findings_store import ReviewFindingsStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.scoped import ScopedConnections


class GardenDeliveryWrites:
    """:func:`write_delivery_plan` over the hub's own delivery, finding, finding-set, and proposal repositories."""

    def __init__(self, connections: ScopedConnections) -> None:
        self.delivery = GardenDeliveryStore(connections)
        self._findings = FindingStore(connections)
        self._finding_sets = FindingSetStore(connections)
        self._proposals = GardenProposalStore(connections)

    def already_delivered(self, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        return self.delivery.already_delivered(chunk_id=chunk_id, node_id=node_id, epoch=epoch)

    def deliver(
        self, plan: DeliveryPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> DeliveryOutcome:
        return write_delivery_plan(
            plan,
            delivery=self.delivery,
            findings=self._findings,
            finding_sets=self._finding_sets,
            proposals=self._proposals,
            admission=admission,
            terminal=terminal,
            transaction=transaction,
        )


class ReviewFindingsWrites:
    """:func:`write_review_plan` over the hub's own review-delivery, finding, and scope repositories."""

    def __init__(self, connections: ScopedConnections) -> None:
        self.delivery = ReviewFindingsStore(connections)
        self._findings = FindingStore(connections)
        self._scopes = ScopeStore(connections)

    def already_delivered(self, *, chunk_id: str) -> bool:
        return self.delivery.already_delivered(chunk_id=chunk_id)

    def deliver(
        self, plan: ReviewFindingsPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> ReviewFindingsOutcome:
        return write_review_plan(
            plan,
            delivery=self.delivery,
            findings=self._findings,
            scopes=self._scopes,
            admission=admission,
            terminal=terminal,
            transaction=transaction,
        )
