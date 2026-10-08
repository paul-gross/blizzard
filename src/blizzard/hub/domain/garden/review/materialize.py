"""Review-finding delivery materialization — turning a
:class:`ValidatedReviewFindings` into the rows a passing delivery mints, written in one
transaction. Mints ids through the injected clock (`bzh:injected-clock`) and builds a
ready-to-insert plan, trusting the validation rather than repeating it."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import Id, IdPrefix
from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.review.validation import ValidatedReviewFindings
from blizzard.hub.domain.graph.model import Node


class ReviewFindingsOutcome(Enum):
    """What :meth:`ReviewFindingsMaterialize.deliver` reports. The first two members mean
    the delivery is durably recorded; ``FENCED`` means ``bzh:epoch-fencing`` refused it and
    nothing landed."""

    RECORDED = "recorded"  # this call minted every row
    ALREADY_RECORDED = "already_recorded"  # a prior call's marker was found; nothing minted
    FENCED = "fenced"


@domain_model
@dataclass(frozen=True)
class NewReviewFinding:
    """A fully-formed ``findings`` row — id already minted (`bzh:domain-takes-objects`).
    Carries no `routine_name`/`introduced`/`introduced_at`: a review-sourced finding has
    no routine lineage and no commit-blame resolution."""

    finding_id: str
    scope_slug: str
    class_: str
    locus: str
    summary: str
    severity: str
    raised_by_chunk_id: str


@domain_model
@dataclass(frozen=True)
class NewReviewFindingFact:
    """A fully-formed ``finding_facts`` row, minus ``recorded_at`` — every fact in one
    delivery shares :attr:`ReviewFindingsPlan.at` (`bzh:injected-clock`). Always an
    `add` fact: only a `deferred` entry reaches materialization."""

    finding_id: str
    ref: str


@domain_model
@dataclass(frozen=True)
class ReviewFindingsPlan:
    """Everything :class:`IWriteReviewFindingsRepository` needs to do its writes — every
    id, timestamp, and scope description already composed (`bzh:injected-clock`). The
    store still mints any unseen scope named here, in the same transaction as the
    findings and facts, but writes this plan's own description rather than its own."""

    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    at: datetime
    new_scope_description: str = ""
    new_findings: list[NewReviewFinding] = field(default_factory=list)
    facts: list[NewReviewFindingFact] = field(default_factory=list)

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
    on ``chunk_id`` alone: a second plan for an already-delivered chunk is dropped."""

    def deliver(self, plan: ReviewFindingsPlan, *, admission: EpochAdmission) -> ReviewFindingsOutcome: ...

    def already_delivered(self, *, chunk_id: str) -> bool:
        """Whether ``chunk_id`` already carries a review-findings-delivered marker — the
        same check :meth:`deliver` makes internally, exposed so a caller can
        short-circuit before re-parsing a replay's artifact."""
        ...


def review_scope_description(chunk: Chunk) -> str:
    """The hub-written description of a scope a review delivery mints by naming it
    (`blizzard-context:/domain/routines-and-scopes.md` §Mint-on-name): it names the chunk
    whose review minted it."""
    return f"Minted by review delivery on chunk {chunk.chunk_id}"


def build_review_plan(
    validated: ValidatedReviewFindings, *, chunk: Chunk, node: Node, epoch: int, at: datetime
) -> ReviewFindingsPlan:
    """The rows a passing review delivery mints, every id minted at `at`: one finding and
    one `add` fact per `deferred` entry, in order, each finding raised by `chunk`."""
    new_findings: list[NewReviewFinding] = []
    facts: list[NewReviewFindingFact] = []
    for entry in validated.deferred:
        finding_id = Id.mint_at(IdPrefix.FINDING, at).value
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
        facts.append(NewReviewFindingFact(finding_id=finding_id, ref=entry.ref))
    return ReviewFindingsPlan(
        chunk_id=chunk.chunk_id,
        node_id=node.node_id,
        node_name=node.name,
        epoch=epoch,
        at=at,
        new_scope_description=review_scope_description(chunk),
        new_findings=new_findings,
        facts=facts,
    )


def review_replay_outcome(*, recorded: bool) -> ReviewFindingsOutcome | None:
    """The review delivery verb by its chunk's marker state: a review raises findings once per chunk
    (`blizzard-context:/domain/findings-and-proposals.md`), so once recorded every later delivery is
    ``ALREADY_RECORDED``, minting nothing and never re-validated. Not yet recorded, `None`: validate and
    write, which records it or is ``FENCED``."""
    return ReviewFindingsOutcome.ALREADY_RECORDED if recorded else None


class ReviewFindingsMaterialize:
    """Turns a :class:`ValidatedReviewFindings` into a :class:`ReviewFindingsPlan`
    (:func:`build_review_plan`, at the clock's instant) and hands it to the store, one
    call — every id minted before the store sees it (`bzh:domain-takes-objects`)."""

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
        plan = build_review_plan(validated, chunk=chunk, node=node, epoch=epoch, at=self._clock.now())
        return self._delivery.deliver(plan, admission=EpochAdmission.AT_OR_ABOVE)
