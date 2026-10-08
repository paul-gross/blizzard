"""Garden-proposal-closure-triggered finding resolutions: delivery-triggered closure,
when the item an accepted proposal minted is delivered, and
the worker-facing read, the findings a chunk's own accepted, minted
proposal answers. Delivery closure is gated on `has_delivery_for_proposal`, not any one
finding's current state, so a crash-retry
(`blizzard-context:/architecture/crash-correctness/hub.md`) still completes an
interrupted closure and a later reopen is never silently redone."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass

from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk, WorkRef
from blizzard.hub.domain.garden.findings.model import Finding, IFindingExitResolver, IReadFindingRepository
from blizzard.hub.domain.garden.proposals.closure import GardenProposalClosure, IReadGardenProposalClosureRepository
from blizzard.hub.domain.garden.proposals.model import GardenProposal, IReadGardenProposalRepository

_log = get_logger("blizzard.hub.garden_proposal_resolution")


def resolve_proposal_findings(
    findings: IReadFindingRepository, finding_ids: Sequence[str], *, live_only: bool = False
) -> list[Finding]:
    """`finding_ids` resolved to their loaded `Finding` rows, in `finding_ids`' own
    order, silently dropping an id that no longer resolves; `live_only` additionally
    drops one a delivery cannot close."""
    return ordered_findings(finding_ids, findings.get_many(finding_ids), live_only=live_only)


def ordered_findings(
    finding_ids: Sequence[str], by_id: Mapping[str, Finding], *, live_only: bool = False
) -> list[Finding]:
    """The findings of `by_id` that `finding_ids` names, in `finding_ids`' order, dropping
    an id `by_id` lacks; `live_only` additionally drops one :meth:`Finding.allows` refuses
    `delivered`."""
    rows = (by_id.get(fid) for fid in finding_ids)
    return [f for f in rows if f is not None and (not live_only or f.allows("delivered"))]


@domain_model
@dataclass(frozen=True)
class DeliveryClosure:
    """The findings a delivered item closes to `delivered`, with the note and actor the
    closing facts carry and the proposal they answer."""

    findings: list[Finding]
    note: str
    actor: str
    proposal_id: str


def delivery_closure(
    closure: GardenProposalClosure,
    proposal: GardenProposal,
    by_id: Mapping[str, Finding],
    pointer: WorkRef,
) -> DeliveryClosure | None:
    """What delivering `pointer`'s item closes: `proposal`'s still-live findings, in its own order,
    delivered on the accepter's behalf (`blizzard-context:/domain/findings-and-proposals.md` §Closing a
    proposal). `None` when `closure` is not the accept that minted the item, or no finding it names is
    still live; the model's own skip (:meth:`Finding.deliver_fact`) leaves the rest as they stand."""
    if not closure.mints_delivery:
        return None
    live = ordered_findings(proposal.findings, by_id, live_only=True)
    if not live:
        return None
    return DeliveryClosure(
        findings=live,
        note=f"delivered by {pointer.source}:{pointer.ref}",
        actor=closure.closed_by,
        proposal_id=closure.proposal_id,
    )


class GardenProposalDeliveryResolution:
    """Closes an accepted, minted proposal's still-live findings to `delivered` when its
    own item is delivered — the owning routine's next run is what settles
    them for good."""

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
        this method has already closed once (`has_delivery_for_proposal`) all resolve
        nothing."""
        closure = self._closures.find_by_item(pointer.source, pointer.ref)
        if closure is None or not closure.mints_delivery:
            return
        if self._findings.has_delivery_for_proposal(closure.proposal_id):
            return
        proposal = self._proposals.get(closure.proposal_id)
        if proposal is None:
            return
        decided = delivery_closure(closure, proposal, self._findings.get_many(proposal.findings), pointer)
        if decided is None:
            return
        self._exits.deliver(decided.findings, note=decided.note, actor=decided.actor, proposal_id=decided.proposal_id)
        _log.info(
            "delivery-triggered finding closure",
            proposal_id=closure.proposal_id,
            source=pointer.source,
            ref=pointer.ref,
            delivered=len(decided.findings),
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
        resolves. Reads only :meth:`Chunk.originating_ref`, so a chunk that absorbed a
        garden-minted item's ref via a later fold resolves `None`, not the folded-in proposal."""
        pointer = chunk.originating_ref()
        if pointer is None:
            return None
        closure = self._closures.find_by_item(pointer.source, pointer.ref)
        if closure is None or not closure.mints_delivery:
            return None
        proposal = self._proposals.get(closure.proposal_id)
        if proposal is None:
            return None
        return resolve_proposal_findings(self._findings, proposal.findings)
