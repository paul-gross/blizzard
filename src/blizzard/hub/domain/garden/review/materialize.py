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
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.chunk.terminal import terminal_under_lock
from blizzard.hub.domain.config.changes import ChangeContext, Door
from blizzard.hub.domain.garden.findings.model import FindingFactInsert, FindingInsert, IWriteFindingRepository
from blizzard.hub.domain.garden.review.validation import ValidatedReviewFindings
from blizzard.hub.domain.garden.scopes import IWriteScopeRepository, Scope, ScopeMint, ScopeSlug
from blizzard.hub.domain.graph.model import Node
from blizzard.hub.domain.kernel.transactions import IHubTransactions, LockedTransaction


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


#: Who mints the scope a review delivery names: the hub itself, through the delivering runner's API call.
REVIEW_MINT_CONTEXT = ChangeContext(actor="system", door=Door.API)


@domain_model
@dataclass(frozen=True)
class ReviewFindingsPlan:
    """Everything :class:`IWriteReviewFindingsRepository` needs to do its writes — every
    id, timestamp, and scope description already composed (`bzh:injected-clock`)."""

    chunk_id: str
    node_id: str
    node_name: str
    epoch: int
    at: datetime
    new_scope_description: str = ""
    new_findings: list[NewReviewFinding] = field(default_factory=list[NewReviewFinding])
    facts: list[NewReviewFindingFact] = field(default_factory=list[NewReviewFindingFact])

    def scope_mint(self, slug: str, project_id: str) -> ScopeMint:
        """The mint of the scope ``slug`` in ``project_id`` — the reviewed chunk's project, read in the delivery's
        own unit — with its ``create`` change, stamped at this plan's instant."""
        return Scope.new(
            ScopeSlug.parse(slug), self.new_scope_description, REVIEW_MINT_CONTEXT, project_id=project_id, at=self.at
        )

    @property
    def scope_slugs(self) -> list[str]:
        """Every distinct scope this plan's findings name, in first-seen order — what
        the delivery ensures exists before inserting a finding into it."""
        seen: dict[str, None] = {}
        for finding in self.new_findings:
            seen.setdefault(finding.scope_slug, None)
        return list(seen)


@domain_model
@dataclass(frozen=True)
class ReviewAdmission:
    """A :class:`ReviewFindingsPlan` its marker and fence reads admit: the reviewed chunk's project, read in the
    delivery's own unit."""

    project_id: str


# --- Repository seam (I-prefix — bzh:repository-split) ---------------------------


class IReadReviewFindingsRepository(Protocol):
    """Read-only review-findings-delivery access — the replay probe a caller makes before writing."""

    def already_delivered(self, *, chunk_id: str) -> bool:
        """Whether ``chunk_id`` already carries a review-findings-delivered marker — the
        same check :meth:`IWriteReviewFindingsRepository.admit` makes."""
        ...


class IWriteReviewFindingsRepository(IReadReviewFindingsRepository, Protocol):
    """The review delivery's own marker, keyed on ``chunk_id`` alone, and its fence: a second plan for an
    already-delivered chunk is dropped. Every row a delivery mints is written by the repository that owns it."""

    def admit(
        self, plan: ReviewFindingsPlan, *, admission: EpochAdmission, terminal: bool, transaction: LockedTransaction
    ) -> ReviewFindingsOutcome | ReviewAdmission:
        """Read in ``transaction``, a unit that locks ``plan.chunk_id``: ``ALREADY_RECORDED`` when the marker
        exists, ``FENCED`` when the fence refuses, else the reviewed chunk's project."""
        ...

    def record_marker(self, plan: ReviewFindingsPlan, *, transaction: LockedTransaction) -> None:
        """Write the chunk's marker in ``transaction``, which locks ``plan.chunk_id``."""
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


def write_review_plan(
    plan: ReviewFindingsPlan,
    *,
    delivery: IWriteReviewFindingsRepository,
    findings: IWriteFindingRepository,
    scopes: IWriteScopeRepository,
    admission: EpochAdmission,
    terminal: bool,
    transaction: LockedTransaction,
) -> ReviewFindingsOutcome:
    """Materialize ``plan`` in ``transaction``, a unit that locks ``plan.chunk_id``: every scope it names that the
    reviewed chunk's project lacks, minted first-write-wins by :meth:`ReviewFindingsPlan.scope_mint`, then its
    ``findings``, then their ``finding_facts``, the marker last."""
    admitted = delivery.admit(plan, admission=admission, terminal=terminal, transaction=transaction)
    if isinstance(admitted, ReviewFindingsOutcome):
        return admitted
    for scope_slug in plan.scope_slugs:
        record, change = plan.scope_mint(scope_slug, admitted.project_id)
        scopes.ensure(record, change=change, transaction=transaction)
    findings.insert_many(
        [
            FindingInsert(
                finding_id=f.finding_id,
                project_id=admitted.project_id,
                routine_name=None,
                scope_slug=f.scope_slug,
                class_=f.class_,
                locus=f.locus,
                summary=f.summary,
                introduced=None,
                introduced_at=None,
                source="review",
                severity=f.severity,
                raised_by_chunk_id=f.raised_by_chunk_id,
            )
            for f in plan.new_findings
        ],
        transaction=transaction,
    )
    findings.insert_facts(
        [
            # A review delta mints no finding set.
            FindingFactInsert(
                finding_id=fact.finding_id, kind="add", recorded_at=plan.at, finding_set_id=None, ref=fact.ref
            )
            for fact in plan.facts
        ],
        transaction=transaction,
    )
    delivery.record_marker(plan, transaction=transaction)
    return ReviewFindingsOutcome.RECORDED


class ReviewFindingsMaterialize:
    """Turns a :class:`ValidatedReviewFindings` into a :class:`ReviewFindingsPlan`
    (:func:`build_review_plan`, at the clock's instant) and writes it in one unit through
    :func:`write_review_plan` — every id minted before a repository sees it (`bzh:domain-takes-objects`)."""

    def __init__(
        self,
        *,
        delivery: IWriteReviewFindingsRepository,
        findings: IWriteFindingRepository,
        scopes: IWriteScopeRepository,
        facts: IReadChunkFactsRepository,
        transactions: IHubTransactions,
        clock: IClock,
    ) -> None:
        self._delivery = delivery
        self._findings = findings
        self._scopes = scopes
        self._facts = facts
        self._transactions = transactions
        self._clock = clock

    def already_delivered(self, *, chunk: Chunk) -> bool:
        return self._delivery.already_delivered(chunk_id=chunk.chunk_id)

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
        with self._transactions.begin() as unit:
            locked = unit.lock_chunks([plan.chunk_id])
            return write_review_plan(
                plan,
                delivery=self._delivery,
                findings=self._findings,
                scopes=self._scopes,
                admission=EpochAdmission.AT_OR_ABOVE,
                terminal=terminal_under_lock(self._facts, plan.chunk_id, transaction=locked),
                transaction=locked,
            )
