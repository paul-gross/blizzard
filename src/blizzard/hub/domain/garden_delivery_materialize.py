"""Delivery materialization (blizzard#393 Phase 3) — turning a :class:`ValidatedDelivery`
into the rows a passing delivery mints, written in one transaction
(blizzard-product:/plans/garden/machinery.md §Delivery). Sibling to ``garden_delivery.py``
rather than folded into it so that module stays pure validation with no I/O; this one
mints ids, resolves a proposal's submission-local ref citations against them, and hands
a ready-to-insert plan to the store, trusting that validation rather than repeating it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import FINDING_PREFIX, FINDING_SET_PREFIX, GARDEN_PROPOSAL_PREFIX, Id
from blizzard.hub.domain.garden_delivery import ValidatedDelivery, is_finding_id_shaped, single_repo_of
from blizzard.hub.domain.graph import Node
from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import Chunk
from blizzard.wire.finding import AddFindingOp, GoneFindingOp, ObservedFindingOp


class DeliveryOutcome(Enum):
    """What :meth:`GardenDelivery.deliver` reports. Both members mean the delivery is
    durably recorded (blizzard-product:/plans/garden/machinery.md §Delivery: "a replay
    finds it and returns `recorded` having minted nothing") — the distinction is kept only
    because it is useful to assert on in tests, never because a caller need branch on it."""

    RECORDED = "recorded"  # this call minted every row
    ALREADY_RECORDED = "already_recorded"  # a prior call's marker was found; nothing minted


@dataclass(frozen=True)
class NewFinding:
    """A fully-formed ``findings`` row — id already minted (`bzh:domain-takes-objects`)."""

    finding_id: str
    routine_name: str
    scope_slug: str
    class_: str
    locus: str
    summary: str
    introduced: str | None
    #: `introduced`'s own authored instant, already resolved by validation (blizzard#394
    #: D5) — never re-resolved here, so a delivery spends the forge slot at most once.
    introduced_at: datetime | None


@dataclass(frozen=True)
class FindingFactRecord:
    """A fully-formed ``finding_facts`` row, minus ``recorded_at`` — every fact in one
    delivery shares :attr:`DeliveryPlan.at` (`bzh:injected-clock`)."""

    finding_id: str
    kind: str  # add | observed | gone
    finding_set_id: str  # the delivered list this fact belongs to (blizzard#401 D1)
    note: str | None = None
    #: The `add` op's own submission-local ref, when it carried one — never set for
    #: `observed`/`gone`.
    ref: str | None = None


@dataclass(frozen=True)
class NewFindingSet:
    """A fully-formed ``finding_sets`` row, minus ``chunk_id`` — every set in one delivery
    shares :attr:`DeliveryPlan.chunk_id`. One per delivered delta, even an empty one (an
    artifact's scope/revisions/measurement are recorded whether or not it names a finding)."""

    finding_set_id: str
    artifact_id: str
    scope_slug: str
    revisions: dict[str, str]
    measurement: str | None


@dataclass(frozen=True)
class NewProposal:
    """A fully-formed ``garden_proposals`` row plus its ``garden_proposal_findings`` link
    rows (``finding_ids``). ``source_artifact_id``/``ref`` are a delivered proposal's own
    idempotence key, the pair the store dedupes a re-delivery visit against, the way
    ``finding_sets.artifact_id`` already dedupes a delta."""

    proposal_id: str
    routine_name: str
    class_: str
    title: str
    body: str
    source_artifact_id: str
    ref: str
    finding_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DeltaMaterialization:
    """One delivered delta's own contribution to the plan — its `finding_sets` row and
    every `findings`/`finding_facts` row that delta alone produced, grouped so a
    per-artifact idempotence hit (this exact artifact already materialized under an
    earlier visit) skips only this group, never the whole plan."""

    finding_set: NewFindingSet
    new_findings: list[NewFinding] = field(default_factory=list)
    facts: list[FindingFactRecord] = field(default_factory=list)


@dataclass(frozen=True)
class DeliveryPlan:
    """Everything :class:`IWriteGardenDeliveryRepository` needs to do its writes — every
    id already minted, every timestamp already stamped (`bzh:injected-clock`). The store
    still resolves one thing of its own: a proposal's citation of a ref whose delta
    turns out to already be materialized, against the id that earlier visit minted."""

    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    at: datetime
    run: RunContext
    deltas: list[DeltaMaterialization]
    proposals: list[NewProposal]


# --- Repository seam (I-prefix — bzh:repository-split; write-only, this phase mints
# nothing to read back) -------------------------------------------------------------


class IWriteGardenDeliveryRepository(Protocol):
    """Materialize one :class:`DeliveryPlan`, atomically and idempotently, keyed on the
    ``(chunk_id, node_id, epoch)`` marker the plan carries."""

    def deliver(self, plan: DeliveryPlan) -> DeliveryOutcome: ...

    def already_delivered(self, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        """Whether the ``(chunk_id, node_id, epoch)`` marker already exists — the same
        check :meth:`deliver` makes internally, exposed so a caller can short-circuit
        before re-validating a retry's content against state that may have drifted since
        the original, successful attempt (blizzard#394 D3)."""
        ...


class GardenDelivery:
    """Turns a Phase-2 :class:`ValidatedDelivery` into a :class:`DeliveryPlan` and hands
    it to the store, one call — minting every id here (`bzh:domain-takes-objects`, the
    pattern ``GardenProposalAuthoring.create`` already sets) rather than in the store."""

    def __init__(self, *, delivery: IWriteGardenDeliveryRepository, clock: IClock) -> None:
        self._delivery = delivery
        self._clock = clock

    def already_delivered(self, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        return self._delivery.already_delivered(chunk_id=chunk_id, node_id=node_id, epoch=epoch)

    def deliver(
        self,
        validated: ValidatedDelivery,
        *,
        chunk: Chunk,
        node: Node,
        epoch: int,
        delta_artifact_ids: Sequence[str],
        proposal_artifact_ids: Sequence[str] = (),
    ) -> DeliveryOutcome:
        """Materialize `validated`. `delta_artifact_ids`/`proposal_artifact_ids` name the
        artifact each of `validated.deltas`/`validated.proposals` came from, positionally
        parallel — neither carries its own artifact id (Phase 2 doesn't track one).
        `chunk`/`node`/`epoch` identify the delivering node-step, the idempotence
        marker's own key."""
        chunk_id = chunk.chunk_id
        node_id = node.node_id
        node_name = node.name
        at = self._clock.now()
        deltas: list[DeltaMaterialization] = []
        # Each `add` op's `ref` -> the `fin_` id minted for it, across every delta at
        # once: a proposal's citation names no delta of its own.
        finding_id_by_ref: dict[str, str] = {}

        for delta, artifact_id in zip(validated.deltas, delta_artifact_ids, strict=True):
            # Minted before the facts loop below: every fact this delta produces
            # attributes to the set that carried it (blizzard#401 D1).
            finding_set_id = Id.mint(FINDING_SET_PREFIX, self._clock).value
            new_findings: list[NewFinding] = []
            facts: list[FindingFactRecord] = []
            single_repo = single_repo_of(delta)
            for op in delta.findings:
                if isinstance(op, AddFindingOp):
                    finding_id = Id.mint(FINDING_PREFIX, self._clock).value
                    if op.ref is not None:
                        finding_id_by_ref[op.ref] = finding_id
                    introduced_at = (
                        validated.introduced_at.get((single_repo, op.introduced))
                        if op.introduced is not None and single_repo is not None
                        else None
                    )
                    new_findings.append(
                        NewFinding(
                            finding_id=finding_id,
                            routine_name=validated.run.routine_name,
                            scope_slug=delta.scope,
                            class_=op.class_,
                            locus=op.locus,
                            summary=op.summary,
                            introduced=op.introduced,
                            introduced_at=introduced_at,
                        )
                    )
                    facts.append(
                        FindingFactRecord(finding_id=finding_id, kind="add", finding_set_id=finding_set_id, ref=op.ref)
                    )
                elif isinstance(op, ObservedFindingOp):
                    facts.append(
                        FindingFactRecord(finding_id=op.id, kind="observed", finding_set_id=finding_set_id, note=None)
                    )
                else:
                    assert isinstance(op, GoneFindingOp)
                    facts.append(
                        FindingFactRecord(finding_id=op.id, kind="gone", finding_set_id=finding_set_id, note=op.note)
                    )
            # One finding_set per delta, even an empty one (delta.findings == []).
            finding_set = NewFindingSet(
                finding_set_id=finding_set_id,
                artifact_id=artifact_id,
                scope_slug=delta.scope,
                revisions=dict(delta.revisions),
                measurement=delta.measurement,
            )
            deltas.append(DeltaMaterialization(finding_set=finding_set, new_findings=new_findings, facts=facts))

        proposals = [
            NewProposal(
                proposal_id=Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
                routine_name=validated.run.routine_name,
                class_=candidate.class_,
                title=candidate.title,
                body=candidate.body,
                source_artifact_id=artifact_id,
                ref=candidate.ref,
                # A `fin_`-shaped entry is already an id; anything else is a ref, resolved
                # against the id its own `add` op minted above.
                finding_ids=[
                    entry if is_finding_id_shaped(entry) else finding_id_by_ref[entry] for entry in candidate.findings
                ],
            )
            for candidate, artifact_id in zip(validated.proposals, proposal_artifact_ids, strict=True)
        ]

        plan = DeliveryPlan(
            chunk_id=chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            at=at,
            run=validated.run,
            deltas=deltas,
            proposals=proposals,
        )
        return self._delivery.deliver(plan)
