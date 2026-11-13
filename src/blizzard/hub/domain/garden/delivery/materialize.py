"""Delivery materialization — turning a :class:`ValidatedDelivery`
into the rows a passing delivery mints, written in one transaction
(blizzard-product:/delivered/garden/machinery.md §Delivery). Mints ids, resolves a
proposal's submission-local ref citations against them, and builds a ready-to-insert
plan, trusting the validation rather than repeating it."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.terminal import terminal_under_lock
from blizzard.hub.domain.garden.delivery.validation import (
    GardenDeliveryRejected,
    ValidatedDelivery,
    is_finding_id_shaped,
    single_repo_of,
)
from blizzard.hub.domain.garden.findings.model import (
    FindingFactInsert,
    FindingInsert,
    FindingSet,
    IWriteFindingRepository,
    IWriteFindingSetRepository,
    finding_lock,
)
from blizzard.hub.domain.garden.formats import FindingAddOp, FindingGoneOp, FindingObservedOp
from blizzard.hub.domain.garden.proposals.model import DeliveredProposal, IWriteGardenProposalRepository
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.kernel.garden_delivery_fault import GardenDeliveryFault
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction


class DeliveryOutcome(Enum):
    """What :meth:`GardenDelivery.deliver` reports. The first two members mean the delivery is
    durably recorded (blizzard-product:/delivered/garden/machinery.md §Delivery) — the
    distinction only useful to assert on; ``FENCED`` means ``bzh:epoch-fencing`` refused it."""

    RECORDED = "recorded"  # this call minted every row
    ALREADY_RECORDED = "already_recorded"  # a prior call's marker was found; nothing minted
    FENCED = "fenced"
    #: A finding the plan's facts name moved off the state validation judged it in; nothing minted,
    #: not even the marker.
    GUARD_LOST = "guard_lost"


@domain_model
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
    introduced_at: datetime | None  # `introduced`'s authored instant, as validation resolved it


@domain_model
@dataclass(frozen=True)
class NewFindingFact:
    """A fully-formed ``finding_facts`` row, minus ``recorded_at`` — every fact in one
    delivery shares :attr:`DeliveryPlan.at` (`bzh:injected-clock`)."""

    finding_id: str
    kind: str  # add | observed | gone | resolved
    finding_set_id: str  # the delivered list this fact belongs to
    note: str | None = None
    #: The `add` op's own submission-local ref, when it carried one — never set for
    #: `observed`/`gone`.
    ref: str | None = None
    #: The `delivered` fact's own closer, carried onto its settling `resolved` fact.
    actor: str | None = None


@domain_model
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


@domain_model
@dataclass(frozen=True)
class NewProposal:
    """A fully-formed ``garden_proposals`` row plus its ``garden_proposal_findings`` link
    rows (``finding_ids``). ``source_artifact_id``/``ref`` are a delivered proposal's own
    idempotence key."""

    proposal_id: str
    routine_name: str
    class_: str
    title: str
    body: str
    source_artifact_id: str
    ref: str
    finding_ids: list[str] = field(default_factory=list[str])


@domain_model
@dataclass(frozen=True)
class DeltaMaterialization:
    """One delivered delta's own contribution to the plan — its `finding_sets` row and
    every `findings`/`finding_facts` row that delta alone produced, grouped so a
    per-artifact idempotence hit (this exact artifact already materialized under an
    earlier visit) skips only this group, never the whole plan."""

    finding_set: NewFindingSet
    new_findings: list[NewFinding] = field(default_factory=list[NewFinding])
    facts: list[NewFindingFact] = field(default_factory=list[NewFindingFact])


@domain_model
@dataclass(frozen=True)
class DeliveryPlan:
    """Everything :class:`IWriteGardenDeliveryRepository` needs to do its writes — every
    id already minted, every timestamp already stamped (`bzh:injected-clock`)."""

    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    at: datetime
    run: RunContext
    deltas: list[DeltaMaterialization]
    proposals: list[NewProposal]
    #: Finding id → the state validation judged it in, for every finding an `observed`/`gone` op names.
    expect: dict[str, str] = field(default_factory=dict[str, str])


@domain_model
@dataclass(frozen=True)
class DeliverySurvivors:
    """What of a :class:`DeliveryPlan` is still owed once its marker, fence, idempotence, and guard reads pass —
    the deltas and proposals no earlier visit materialized, and how a surviving proposal's citations resolve."""

    deltas: list[DeltaMaterialization]
    proposals: list[NewProposal]
    #: A dropped delta's freshly minted finding id -> the id the earlier visit recorded for the same ref.
    ref_substitutions: dict[str, str] = field(default_factory=dict[str, str])
    #: Every finding a dropped delta minted this visit, which is never inserted.
    uninserted_finding_ids: set[str] = field(default_factory=set[str])

    def links(self) -> list[tuple[str, str]]:
        """Every ``(proposal_id, finding_id)`` link the surviving proposals owe. An unresolved citation names a
        finding this visit never inserted, so its link is dropped like the delta was rather than pointing at
        nothing."""
        return [
            (p.proposal_id, self.ref_substitutions.get(finding_id, finding_id))
            for p in self.proposals
            for finding_id in p.finding_ids
            if finding_id in self.ref_substitutions or finding_id not in self.uninserted_finding_ids
        ]


# --- Repository seam (I-prefix — bzh:repository-split) ---------------------------


class IReadGardenDeliveryRepository(Protocol):
    """Read-only garden-delivery access — the replay probe a caller makes before writing."""

    def already_delivered(self, *, chunk_id: str, node_id: str, epoch: int) -> bool:
        """Whether the ``(chunk_id, node_id, epoch)`` marker already exists — the same
        check :meth:`IWriteGardenDeliveryRepository.admit` makes."""
        ...


class IWriteGardenDeliveryRepository(IReadGardenDeliveryRepository, Protocol):
    """The delivery's own marker, its fence, and the idempotence and guard reads, keyed on the
    ``(chunk_id, node_id, epoch)`` marker the plan carries. Every row a delivery mints is written by
    the repository that owns it."""

    def admit(
        self, plan: DeliveryPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> DeliveryOutcome | DeliverySurvivors:
        """Read in ``transaction``, a unit that locks ``plan.chunk_id`` and the :func:`finding_lock` of every
        finding in ``plan.expect``: ``ALREADY_RECORDED`` when the marker exists, ``FENCED`` when the fence refuses,
        ``GUARD_LOST`` when a finding a surviving fact names moved off its expected state, else what survives. A
        delta whose ``artifact_id`` is already materialized is dropped, and a proposal's citation of a ref in it
        resolves to the id minted then; a proposal already present by ``(source_artifact_id, ref)`` is dropped."""
        ...

    def findings_outside_project(
        self, finding_ids: Sequence[str], project_id: str, *, transaction: LockedTransaction
    ) -> list[str]:
        """The ids among ``finding_ids`` stored in a project other than ``project_id``, sorted, read in
        ``transaction``."""
        ...

    def record_marker(self, plan: DeliveryPlan, *, transaction: LockedTransaction) -> None:
        """Write the ``(chunk_id, node_id, epoch)`` marker in ``transaction``, which locks ``plan.chunk_id``."""
        ...


def build_delivery_plan(
    validated: ValidatedDelivery,
    *,
    chunk: Chunk,
    node: Node,
    epoch: int,
    delta_artifact_ids: Sequence[str],
    proposal_artifact_ids: Sequence[str] = (),
    at: datetime,
) -> DeliveryPlan:
    """The rows a passing delivery mints, every id minted at `at`: one finding set per delta, one finding
    and `add` fact per `add` op, an `observed` fact per `observed` op, and per `gone` op the fact its finding's
    :meth:`Finding.run_gone` names. A non-`fin_` proposal citation resolves against
    the id its `add` op minted. The artifact-id lists parallel `validated.deltas`/`validated.proposals`."""
    chunk_id = chunk.chunk_id
    node_id = node.node_id
    node_name = node.name
    deltas: list[DeltaMaterialization] = []
    # Each `add` op's `ref` -> the `fin_` id minted for it, across every delta at
    # once: a proposal's citation names no delta of its own.
    finding_id_by_ref: dict[str, str] = {}

    for delta, artifact_id in zip(validated.deltas, delta_artifact_ids, strict=True):
        # Minted before the facts loop below: every fact this delta produces
        # attributes to the set that carried it.
        finding_set_id = Id.mint_at(IdPrefix.FINDING_SET, at).value
        new_findings: list[NewFinding] = []
        facts: list[NewFindingFact] = []
        single_repo = single_repo_of(delta)
        for op in delta.findings:
            if isinstance(op, FindingAddOp):
                finding_id = Id.mint_at(IdPrefix.FINDING, at).value
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
                    NewFindingFact(finding_id=finding_id, kind="add", finding_set_id=finding_set_id, ref=op.ref)
                )
            elif isinstance(op, FindingObservedOp):
                facts.append(NewFindingFact(finding_id=op.id, kind="observed", finding_set_id=finding_set_id))
            else:
                assert isinstance(op, FindingGoneOp)
                kind, actor = validated.gone_settlements.get(op.id, ("gone", None))
                facts.append(
                    NewFindingFact(
                        finding_id=op.id, kind=kind, finding_set_id=finding_set_id, note=op.note, actor=actor
                    )
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
            proposal_id=Id.mint_at(IdPrefix.GARDEN_PROPOSAL, at).value,
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

    return DeliveryPlan(
        chunk_id=chunk_id,
        node_id=node_id,
        node_name=node_name,
        epoch=epoch,
        at=at,
        run=validated.run,
        deltas=deltas,
        proposals=proposals,
        expect=dict(validated.expect),
    )


def delivery_replay_outcome(*, recorded: bool) -> DeliveryOutcome | None:
    """The garden delivery verb by its node-step's marker state, keyed
    `(chunk, node, epoch)`. Once recorded, a replay is ``ALREADY_RECORDED``, minting nothing
    and never re-validated against state that may have drifted since. Not yet recorded,
    `None`: validate and write, which records it or is ``FENCED``."""
    return DeliveryOutcome.ALREADY_RECORDED if recorded else None


def write_delivery_plan(
    plan: DeliveryPlan,
    *,
    delivery: IWriteGardenDeliveryRepository,
    findings: IWriteFindingRepository,
    finding_sets: IWriteFindingSetRepository,
    proposals: IWriteGardenProposalRepository,
    admission: EpochAdmission,
    terminal: bool,
    transaction: LockedTransaction,
) -> DeliveryOutcome:
    """Materialize ``plan`` in ``transaction``, a unit that locks ``plan.chunk_id`` and the :func:`finding_lock` of
    every finding in ``plan.expect``: ``findings``, then ``finding_sets``, then ``finding_facts`` (each fact
    references its set), then proposals and their links, the marker last. A proposal citing a finding stored in a
    project other than ``plan.run``'s raises :class:`GardenDeliveryRejected` naming it, before any link row, and the
    unit rolls everything back."""
    admitted = delivery.admit(plan, admission=admission, terminal=terminal, transaction=transaction)
    if isinstance(admitted, DeliveryOutcome):
        return admitted
    project_id = plan.run.project_id
    findings.insert_many(
        [
            FindingInsert(
                finding_id=f.finding_id,
                project_id=project_id,
                routine_name=f.routine_name,
                scope_slug=f.scope_slug,
                class_=f.class_,
                locus=f.locus,
                summary=f.summary,
                introduced=f.introduced,
                introduced_at=f.introduced_at,
                source="routine",
            )
            for d in admitted.deltas
            for f in d.new_findings
        ],
        transaction=transaction,
    )
    finding_sets.insert_many(
        [
            FindingSet(
                finding_set_id=d.finding_set.finding_set_id,
                artifact_id=d.finding_set.artifact_id,
                chunk_id=plan.chunk_id,
                scope_slug=d.finding_set.scope_slug,
                routine_name=plan.run.routine_name,
                revisions=d.finding_set.revisions,
                measurement=d.finding_set.measurement,
                project_id=project_id,
            )
            for d in admitted.deltas
        ],
        transaction=transaction,
    )
    findings.insert_facts(
        [
            FindingFactInsert(
                finding_id=fact.finding_id,
                kind=fact.kind,
                recorded_at=plan.at,
                finding_set_id=fact.finding_set_id,
                note=fact.note,
                ref=fact.ref,
                actor=fact.actor,
            )
            for d in admitted.deltas
            for fact in d.facts
        ],
        transaction=transaction,
    )
    proposals.insert_delivered(
        [
            DeliveredProposal(
                proposal_id=p.proposal_id,
                project_id=project_id,
                routine_name=p.routine_name,
                class_=p.class_,
                title=p.title,
                body=p.body,
                source_artifact_id=p.source_artifact_id,
                ref=p.ref,
                created_at=plan.at,
            )
            for p in admitted.proposals
        ],
        transaction=transaction,
    )
    links = admitted.links()
    if links:
        outside = delivery.findings_outside_project(
            [finding_id for _, finding_id in links], project_id, transaction=transaction
        )
        if outside:
            raise GardenDeliveryRejected(
                f"finding {outside[0]!r} belongs to another project than routine "
                f"{plan.run.routine_name!r}'s — nothing was written",
                GardenDeliveryFault.PROPOSALS,
            )
        proposals.link_findings(links, transaction=transaction)
    # Always written, even when every delta was already materialized: this visit
    # is a genuinely new (chunk_id, node_id, epoch) and owes its idempotence key.
    delivery.record_marker(plan, transaction=transaction)
    if admitted.deltas or admitted.proposals:
        return DeliveryOutcome.RECORDED
    return DeliveryOutcome.ALREADY_RECORDED


class GardenDelivery:
    """Turns a Phase-2 :class:`ValidatedDelivery` into a :class:`DeliveryPlan`
    (:func:`build_delivery_plan`, at the clock's instant) and writes it in one unit through
    :func:`write_delivery_plan` — every id minted before a repository sees it (`bzh:domain-takes-objects`)."""

    def __init__(
        self,
        *,
        delivery: IWriteGardenDeliveryRepository,
        findings: IWriteFindingRepository,
        finding_sets: IWriteFindingSetRepository,
        proposals: IWriteGardenProposalRepository,
        facts: IReadChunkFactsRepository,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._delivery = delivery
        self._findings = findings
        self._finding_sets = finding_sets
        self._proposals = proposals
        self._facts = facts
        self._transactions = transactions
        self._clock = clock

    def already_delivered(self, *, chunk: Chunk, node: Node, epoch: int) -> bool:
        return self._delivery.already_delivered(chunk_id=chunk.chunk_id, node_id=node.node_id, epoch=epoch)

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
        parallel — neither carries its own artifact id.
        `chunk`/`node`/`epoch` identify the delivering node-step, the idempotence
        marker's own key."""
        plan = build_delivery_plan(
            validated,
            chunk=chunk,
            node=node,
            epoch=epoch,
            delta_artifact_ids=delta_artifact_ids,
            proposal_artifact_ids=proposal_artifact_ids,
            at=self._clock.now(),
        )
        with self._transactions.begin() as unit:
            locked = unit.lock_set(chunks=[plan.chunk_id], keys=[finding_lock(fid) for fid in sorted(plan.expect)])
            return write_delivery_plan(
                plan,
                delivery=self._delivery,
                findings=self._findings,
                finding_sets=self._finding_sets,
                proposals=self._proposals,
                admission=EpochAdmission.AT_OR_ABOVE,
                terminal=terminal_under_lock(self._facts, plan.chunk_id, transaction=locked),
                transaction=locked,
            )
