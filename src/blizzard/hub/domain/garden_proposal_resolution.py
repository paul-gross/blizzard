"""Garden-proposal-closure-triggered finding resolutions: delivery-triggered exit
(blizzard#394 Phase 3), when the item an accepted proposal minted is delivered, and the
worker-facing read (blizzard#397 Phase 1), the findings a chunk's own accepted, minted
proposal answers. Delivery resolution is gated on `has_resolution_for_proposal`, not any
one finding's current state, so a crash-retry
(`blizzard-context:/architecture/crash-correctness/hub.md`) still completes an
interrupted resolution and a later reopen is never silently redone."""

from __future__ import annotations

from collections.abc import Sequence

from blizzard.foundation.logging import get_logger
from blizzard.hub.domain.findings import Finding, IFindingExitResolver, IReadFindingRepository
from blizzard.hub.domain.garden_proposal_closure import (
    GardenProposalClosureKind,
    GardenProposalItemOutcome,
    IReadGardenProposalClosureRepository,
)
from blizzard.hub.domain.garden_proposals import IReadGardenProposalRepository
from blizzard.hub.domain.work import Chunk, WorkRef

_log = get_logger("blizzard.hub.garden_proposal_resolution")


def resolve_proposal_findings(
    findings: IReadFindingRepository, finding_ids: Sequence[str], *, live_only: bool = False
) -> list[Finding]:
    """`finding_ids` resolved to their loaded `Finding` rows, in `finding_ids`' own
    order, silently dropping an id that no longer resolves; `live_only` additionally
    drops one whose current state is not `"live"`. The one walk every proposal→findings
    reader (the accept route, the worker-facing read, the delivery-triggered exit) shares
    (blizzard#397), so the three cannot drift on order, missing-id handling, or
    liveness."""
    by_id = findings.get_many(finding_ids)
    rows = (by_id.get(fid) for fid in finding_ids)
    return [f for f in rows if f is not None and (not live_only or f.state == "live")]


class GardenProposalDeliveryResolution:
    """Resolves an accepted, minted proposal's still-live findings when its own item is
    delivered."""

    def __init__(
        self,
        *,
        closures: IReadGardenProposalClosureRepository,
        proposals: IReadGardenProposalRepository,
        findings: IReadFindingRepository,
        exits: IFindingExitResolver,
    ) -> None:
        self._closures = closures
        self._proposals = proposals
        self._findings = findings
        self._exits = exits

    def resolve_for_item(self, pointer: WorkRef) -> None:
        """No-op unless `pointer` is the item an accepted, minting closure names — a
        pass, a declined accept, an item from no garden proposal at all, or a proposal
        this method has already resolved once (`has_resolution_for_proposal`) all resolve
        nothing."""
        closure = self._closures.find_by_item(pointer.source, pointer.ref)
        if closure is None:
            return
        if closure.closure is not GardenProposalClosureKind.ACCEPTED:
            return
        if closure.item_outcome is not GardenProposalItemOutcome.MINTED:
            return
        if self._findings.has_resolution_for_proposal(closure.proposal_id):
            return
        proposal = self._proposals.get(closure.proposal_id)
        if proposal is None:
            return
        live = resolve_proposal_findings(self._findings, proposal.findings, live_only=True)
        if not live:
            return
        self._exits.resolve(
            live,
            note=f"resolved by delivery of {pointer.source}:{pointer.ref}",
            actor=closure.closed_by,
            proposal_id=closure.proposal_id,
        )
        _log.info(
            "delivery-triggered finding resolution",
            proposal_id=closure.proposal_id,
            source=pointer.source,
            ref=pointer.ref,
            resolved=len(live),
        )


class AnsweredFindingsReader:
    """Reads the findings `chunk`'s own accepted, minted garden proposal answers — the
    worker-facing read a leased chunk can reach its own findings through, sibling to
    :class:`GardenProposalDeliveryResolution`'s write-side walk but read-only and keyed
    off the chunk itself rather than a delivered pointer. Named apart from the module's
    other "resolution" — that word already names the delivery-triggered exit write."""

    def __init__(
        self,
        *,
        closures: IReadGardenProposalClosureRepository,
        proposals: IReadGardenProposalRepository,
        findings: IReadFindingRepository,
    ) -> None:
        self._closures = closures
        self._proposals = proposals
        self._findings = findings

    def resolve_for_chunk(self, chunk: Chunk) -> list[Finding] | None:
        """The findings `chunk`'s own proposal answers, in the proposal's own order —
        `None` when `chunk` carries no work ref, its item names no closure, that closure
        is a pass or a declined accept, or the closure names a proposal that no longer
        resolves. Reads only `chunk.work_refs[0]`, the same single-ref lookup
        `IReadRunContextRepository.for_chunk` already makes (`run_context.py`) — a chunk
        that absorbed a garden-minted item's ref via a later fold resolves `None` here
        too, rather than the folded-in proposal."""
        if not chunk.work_refs:
            return None
        pointer = chunk.work_refs[0]
        closure = self._closures.find_by_item(pointer.source, pointer.ref)
        if closure is None:
            return None
        if closure.closure is not GardenProposalClosureKind.ACCEPTED:
            return None
        if closure.item_outcome is not GardenProposalItemOutcome.MINTED:
            return None
        proposal = self._proposals.get(closure.proposal_id)
        if proposal is None:
            return None
        return resolve_proposal_findings(self._findings, proposal.findings)
