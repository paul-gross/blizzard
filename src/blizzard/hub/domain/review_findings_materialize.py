"""Review-finding delivery materialization (blizzard#582) — turning a
:class:`ValidatedReviewFindings` into the rows a passing delivery mints, written in one
transaction. Sibling to `review_findings.py` rather than folded into it so that module
stays pure validation with no I/O; this one mints ids through the injected clock
(`bzh:injected-clock`) and hands a ready-to-insert plan to the store, trusting that
validation rather than repeating it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import FINDING_PREFIX, Id
from blizzard.hub.domain.graph import Node
from blizzard.hub.domain.review_findings import ValidatedReviewFindings
from blizzard.hub.domain.work import Chunk


class ReviewFindingsOutcome(Enum):
    """What :meth:`ReviewFindingsMaterialize.deliver` reports. Both members mean the
    delivery is durably recorded — the distinction exists only to assert on in tests,
    mirroring `garden_delivery_materialize.DeliveryOutcome`."""

    RECORDED = "recorded"  # this call minted every row
    ALREADY_RECORDED = "already_recorded"  # a prior call's marker was found; nothing minted


@dataclass(frozen=True)
class NewReviewFinding:
    """A fully-formed ``findings`` row — id already minted (`bzh:domain-takes-objects`).
    Carries no `routine_name`/`introduced`/`introduced_at`: a review-sourced finding has
    no routine lineage and no commit-blame resolution (blizzard#582 D1)."""

    finding_id: str
    scope_slug: str
    class_: str
    locus: str
    summary: str
    severity: str
    raised_by_chunk_id: str


@dataclass(frozen=True)
class ReviewFindingFactRecord:
    """A fully-formed ``finding_facts`` row, minus ``recorded_at`` — every fact in one
    delivery shares :attr:`ReviewFindingsPlan.at` (`bzh:injected-clock`). Always an
    `add` fact: only a `deferred` entry reaches materialization."""

    finding_id: str
    ref: str


@dataclass(frozen=True)
class ReviewFindingsPlan:
    """Everything :class:`IWriteReviewFindingsRepository` needs to do its writes — every
    id already minted, every timestamp already stamped (`bzh:injected-clock`). The store
    still mints any scope named here that it has not seen before, inside the same
    transaction as the findings and facts (blizzard#582 D6) — a deliberate deviation from
    the garden-delivery precedent, which reads the scope and refuses rather than minting it."""

    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    at: datetime
    new_findings: list[NewReviewFinding] = field(default_factory=list)
    facts: list[ReviewFindingFactRecord] = field(default_factory=list)

    @property
    def scope_slugs(self) -> list[str]:
        """Every distinct scope this plan's findings name, in first-seen order — what
        the store ensures exists before inserting a finding into it."""
        seen: dict[str, None] = {}
        for finding in self.new_findings:
            seen.setdefault(finding.scope_slug, None)
        return list(seen)


# --- Repository seam (I-prefix — bzh:repository-split; write-only, this phase mints
# nothing to read back) -------------------------------------------------------------


class IWriteReviewFindingsRepository(Protocol):
    """Materialize one :class:`ReviewFindingsPlan`, atomically and idempotently, keyed
    on ``chunk_id`` alone (blizzard#582 D6) — not ``(chunk_id, node_id, epoch)``, since a
    chunk may reach `record-findings` more than once only through a rare post-landing
    repair round, whose deferred findings this idempotence key deliberately drops."""

    def deliver(self, plan: ReviewFindingsPlan) -> ReviewFindingsOutcome: ...

    def already_delivered(self, *, chunk_id: str) -> bool:
        """Whether ``chunk_id`` already carries a review-findings-delivered marker — the
        same check :meth:`deliver` makes internally, exposed so a caller can
        short-circuit before re-parsing a replay's artifact."""
        ...


class ReviewFindingsMaterialize:
    """Turns a :class:`ValidatedReviewFindings` into a :class:`ReviewFindingsPlan` and
    hands it to the store, one call — minting every id here (`bzh:domain-takes-objects`)
    rather than in the store."""

    def __init__(self, *, delivery: IWriteReviewFindingsRepository, clock: IClock) -> None:
        self._delivery = delivery
        self._clock = clock

    def already_delivered(self, *, chunk_id: str) -> bool:
        return self._delivery.already_delivered(chunk_id=chunk_id)

    def deliver(
        self,
        validated: ValidatedReviewFindings,
        *,
        chunk: Chunk,
        node: Node,
        epoch: int,
    ) -> ReviewFindingsOutcome:
        """Materialize `validated`. `chunk`/`node`/`epoch` identify the delivering
        node-step, recorded on the marker row for legibility even though the
        idempotence key itself is `chunk_id` alone."""
        at = self._clock.now()
        new_findings: list[NewReviewFinding] = []
        facts: list[ReviewFindingFactRecord] = []
        for entry in validated.deferred:
            assert entry.severity is not None
            assert entry.scope is not None
            assert entry.class_ is not None
            assert entry.locus is not None
            assert entry.summary is not None
            finding_id = Id.mint(FINDING_PREFIX, self._clock).value
            new_findings.append(
                NewReviewFinding(
                    finding_id=finding_id,
                    scope_slug=entry.scope,
                    class_=entry.class_,
                    locus=entry.locus,
                    summary=entry.summary,
                    severity=entry.severity,
                    raised_by_chunk_id=chunk.chunk_id,
                )
            )
            facts.append(ReviewFindingFactRecord(finding_id=finding_id, ref=entry.ref))

        plan = ReviewFindingsPlan(
            chunk_id=chunk.chunk_id,
            node_id=node.node_id,
            node_name=node.name,
            epoch=epoch,
            at=at,
            new_findings=new_findings,
            facts=facts,
        )
        return self._delivery.deliver(plan)
