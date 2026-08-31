"""Delivery-triggered finding resolution (blizzard#394 Phase 3): when the item an
accepted garden proposal minted is delivered, its still-live findings resolve,
attributed to it. Re-entrant by construction — only a `live` finding is handed to
`FindingExitService.resolve` — so a drain retried after a crash
(`blizzard-context:/architecture/crash-correctness/hub.md`) resolves only what an
earlier pass left live."""

from __future__ import annotations

from blizzard.hub.domain.findings import FindingExitService, IReadFindingRepository
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    IReadGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden_proposals import IReadGardenProposalRepository
from blizzard.hub.domain.work import WorkRef


class GardenProposalDeliveryResolution:
    """Resolves an accepted, minted proposal's still-live findings when its own item is
    delivered."""

    def __init__(
        self,
        *,
        closures: IReadGardenProposalClosureRepository,
        proposals: IReadGardenProposalRepository,
        findings: IReadFindingRepository,
        exits: FindingExitService,
    ) -> None:
        self._closures = closures
        self._proposals = proposals
        self._findings = findings
        self._exits = exits

    def resolve_for_item(self, pointer: WorkRef) -> None:
        """No-op unless `pointer` is the item an accepted, minting closure names — a
        pass, a declined accept, or an item from no garden proposal at all all resolve
        nothing."""
        closure = self._closures.find_by_item(pointer.source, pointer.ref)
        if closure is None:
            return
        if closure.closure is not GardenProposalClosureKind.ACCEPTED:
            return
        if closure.item_outcome is not GardenProposalItemOutcome.MINTED:
            return
        proposal = self._proposals.get(closure.proposal_id)
        if proposal is None:
            return
        live = [f for fid in proposal.findings if (f := self._findings.get(fid)) is not None and f.state == "live"]
        if not live:
            return
        self._exits.resolve(
            live,
            note=f"resolved by delivery of {pointer.source}:{pointer.ref}",
            actor=closure.closed_by,
            proposal_id=closure.proposal_id,
        )
