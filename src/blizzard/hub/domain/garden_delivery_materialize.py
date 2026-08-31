"""Delivery materialization (blizzard#393 Phase 3) — turning a :class:`ValidatedDelivery`
into the rows a passing delivery mints, written in one transaction
(blizzard-product:/plans/garden/machinery.md §Delivery). Sibling to ``garden_delivery.py``
rather than folded into it so that module stays pure validation with no I/O; this one
mints ids and hands a ready-to-insert plan to the store."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import FINDING_PREFIX, FINDING_SET_PREFIX, GARDEN_PROPOSAL_PREFIX, Id
from blizzard.hub.domain.garden_delivery import ValidatedDelivery
from blizzard.hub.domain.run_context import RunContext
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


@dataclass(frozen=True)
class FindingFactRecord:
    """A fully-formed ``finding_facts`` row, minus ``recorded_at`` — every fact in one
    delivery shares :attr:`DeliveryPlan.at` (`bzh:injected-clock`)."""

    finding_id: str
    kind: str  # add | observed | gone
    note: str | None = None


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
    rows (``finding_ids``)."""

    proposal_id: str
    routine_name: str
    class_: str
    title: str
    body: str
    finding_ids: list[str] = field(default_factory=list)


@dataclass(frozen=True)
class DeliveryPlan:
    """Everything :class:`IWriteGardenDeliveryRepository` needs to do its writes with no
    further computation — every id already minted, every timestamp already stamped
    (`bzh:injected-clock`), so the store adapter is pure "insert these rows"."""

    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    at: datetime
    run: RunContext
    new_findings: list[NewFinding]
    facts: list[FindingFactRecord]
    finding_sets: list[NewFindingSet]
    proposals: list[NewProposal]


# --- Repository seam (I-prefix — bzh:repository-split; write-only, this phase mints
# nothing to read back) -------------------------------------------------------------


class IWriteGardenDeliveryRepository(Protocol):
    """Materialize one :class:`DeliveryPlan`, atomically and idempotently, keyed on the
    ``(chunk_id, node_id, epoch)`` marker the plan carries."""

    def deliver(self, plan: DeliveryPlan) -> DeliveryOutcome: ...


class GardenDelivery:
    """Turns a Phase-2 :class:`ValidatedDelivery` into a :class:`DeliveryPlan` and hands
    it to the store, one call — minting every id here (`bzh:domain-takes-objects`, the
    pattern ``GardenProposalAuthoring.create`` already sets) rather than in the store."""

    def __init__(self, *, delivery: IWriteGardenDeliveryRepository, clock: IClock) -> None:
        self._delivery = delivery
        self._clock = clock

    def deliver(
        self,
        validated: ValidatedDelivery,
        *,
        chunk_id: str,
        node_id: str,
        node_name: str,
        epoch: int,
        delta_artifact_ids: Sequence[str],
    ) -> DeliveryOutcome:
        """Materialize `validated`. `delta_artifact_ids` names the artifact each of
        `validated.deltas` came from, positionally parallel to it — `validated.deltas`
        itself carries no artifact id (Phase 2 doesn't track one). `chunk_id`/`node_id`/
        `node_name`/`epoch` identify the delivering node-step, the idempotence marker's
        own key."""
        at = self._clock.now()
        new_findings: list[NewFinding] = []
        facts: list[FindingFactRecord] = []
        finding_sets: list[NewFindingSet] = []

        for delta, artifact_id in zip(validated.deltas, delta_artifact_ids, strict=True):
            for op in delta.findings:
                if isinstance(op, AddFindingOp):
                    finding_id = Id.mint(FINDING_PREFIX, self._clock).value
                    new_findings.append(
                        NewFinding(
                            finding_id=finding_id,
                            routine_name=validated.run.routine_name,
                            scope_slug=delta.scope,
                            class_=op.class_,
                            locus=op.locus,
                            summary=op.summary,
                            introduced=op.introduced,
                        )
                    )
                    facts.append(FindingFactRecord(finding_id=finding_id, kind="add", note=None))
                elif isinstance(op, ObservedFindingOp):
                    facts.append(FindingFactRecord(finding_id=op.id, kind="observed", note=None))
                else:
                    assert isinstance(op, GoneFindingOp)
                    facts.append(FindingFactRecord(finding_id=op.id, kind="gone", note=op.note))
            # One finding_set per delta, even an empty one (delta.findings == []).
            finding_sets.append(
                NewFindingSet(
                    finding_set_id=Id.mint(FINDING_SET_PREFIX, self._clock).value,
                    artifact_id=artifact_id,
                    scope_slug=delta.scope,
                    revisions=dict(delta.revisions),
                    measurement=delta.measurement,
                )
            )

        proposals = [
            NewProposal(
                proposal_id=Id.mint(GARDEN_PROPOSAL_PREFIX, self._clock).value,
                routine_name=validated.run.routine_name,
                class_=candidate.class_,
                title=candidate.title,
                body=candidate.body,
                finding_ids=list(candidate.findings),
            )
            for candidate in validated.proposals
        ]

        plan = DeliveryPlan(
            chunk_id=chunk_id,
            node_id=node_id,
            node_name=node_name,
            epoch=epoch,
            at=at,
            run=validated.run,
            new_findings=new_findings,
            facts=facts,
            finding_sets=finding_sets,
            proposals=proposals,
        )
        return self._delivery.deliver(plan)
