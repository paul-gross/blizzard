"""Garden-proposal closure — the two verbs that end a proposal's life:
**pass** (considered and declined, with a reason) and **accept** (agreed, minting a
linked hub work item by default). Closure is terminal, mirroring
:class:`~blizzard.foundation.work_items.WorkItemClosure`. Takes an already-loaded
:class:`~blizzard.hub.domain.garden.proposals.model.GardenProposal` (``bzh:domain-takes-objects``)."""

from __future__ import annotations

from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NoReturn, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.garden_proposals import GardenProposalClosureKind, GardenProposalItemOutcome
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.chunk.model import HubWorkItem, WorkItemAuthor
from blizzard.hub.domain.garden.findings.model import Finding
from blizzard.hub.domain.garden.proposals.model import (
    GardenProposal,
    GardenProposalAlreadyClosed,
    GardenProposalState,
    GardenProposalVerb,
    garden_proposal_state,
    verb_legal_from,
)
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.work_items.editing import WorkItemEditService


class GardenProposalCountBucket(StrEnum):
    """Which of the four garden-proposal-count buckets a proposal falls
    into, classified by its current closure state — never stored, always derived by
    :func:`classify_proposal_count_bucket`."""

    OPEN = "open"
    PASSED = "passed"
    ACCEPTED_WITH_ITEM = "accepted_with_item"
    ACCEPTED_WITHOUT_ITEM = "accepted_without_item"


def classify_proposal_count_bucket(
    closure: GardenProposalClosureKind | None, item_outcome: GardenProposalItemOutcome | None
) -> GardenProposalCountBucket:
    """A proposal's count bucket from its current closure state: no
    closure is `OPEN`, `PASSED` is `PASSED`, and `ACCEPTED` splits on `item_outcome`
    into `ACCEPTED_WITH_ITEM`/`ACCEPTED_WITHOUT_ITEM`. Raises rather than misclassify
    an `ACCEPTED` closure with no `item_outcome` — `GardenProposalClosureService.accept`
    never leaves one unset."""
    if closure is None:
        return GardenProposalCountBucket.OPEN
    if closure is GardenProposalClosureKind.PASSED:
        return GardenProposalCountBucket.PASSED
    if item_outcome is GardenProposalItemOutcome.MINTED:
        return GardenProposalCountBucket.ACCEPTED_WITH_ITEM
    if item_outcome is GardenProposalItemOutcome.DECLINED:
        return GardenProposalCountBucket.ACCEPTED_WITHOUT_ITEM
    raise ValueError(f"accepted closure carries no item_outcome: {closure!r}")


@domain_model
@dataclass(frozen=True)
class GardenProposalClosure:
    """One garden proposal's closing record — a pass or an accept, either way terminal."""

    proposal_id: str
    closure: GardenProposalClosureKind
    reason: str | None
    closed_by: str
    closed_at: datetime
    item_outcome: GardenProposalItemOutcome | None
    source: str | None
    ref: str | None

    @property
    def state(self) -> GardenProposalState:
        """The state this closure puts its proposal in."""
        return garden_proposal_state(self.closure, self.item_outcome)

    @property
    def mints_delivery(self) -> bool:
        """Whether delivering the item this closure names reaches its proposal — true only
        for an accept that minted that item."""
        return verb_legal_from(self.state, GardenProposalVerb.DELIVER)

    @classmethod
    def passing(
        cls,
        proposal: GardenProposal,
        existing: GardenProposalClosure | None,
        *,
        reason: str,
        by: str,
        at: datetime,
    ) -> GardenProposalClosure:
        """The passed closure for `proposal`, its reason stored stripped. Refuses a closed
        proposal first (:class:`GardenProposalAlreadyClosed`), as every verb on a closed
        proposal does, then a blank reason (:class:`GardenProposalPassReasonRequired`)."""
        proposal.require_legal(GardenProposalVerb.PASS, existing)
        text = reason.strip()
        if not text:
            raise GardenProposalPassReasonRequired()
        return cls(
            proposal_id=proposal.proposal_id,
            closure=GardenProposalClosureKind.PASSED,
            reason=text,
            closed_by=by,
            closed_at=at,
            item_outcome=None,
            source=None,
            ref=None,
        )

    @classmethod
    def accepted_declining(
        cls,
        proposal: GardenProposal,
        existing: GardenProposalClosure | None,
        *,
        reason: str | None,
        body: str | None,
        by: str,
        at: datetime,
    ) -> GardenProposalClosure:
        """The closure of an accept that mints no item. Refuses a closed proposal first,
        then a `body` override (:class:`GardenProposalBodyWithoutMint`) — an override
        only a minted item could carry. The reason is optional: stored stripped, a blank
        one as none."""
        proposal.require_legal(GardenProposalVerb.ACCEPT, existing)
        if body is not None:
            raise GardenProposalBodyWithoutMint(proposal.proposal_id)
        return cls(
            proposal_id=proposal.proposal_id,
            closure=GardenProposalClosureKind.ACCEPTED,
            reason=accept_reason(reason),
            closed_by=by,
            closed_at=at,
            item_outcome=GardenProposalItemOutcome.DECLINED,
            source=None,
            ref=None,
        )

    @classmethod
    def accepted_minting(cls, accept: MintingAccept, item: HubWorkItem, *, by: str) -> GardenProposalClosure:
        """The closure of an accept that minted `item`, stamped at the item's own
        creation."""
        return cls(
            proposal_id=accept.proposal_id,
            closure=GardenProposalClosureKind.ACCEPTED,
            reason=accept.reason,
            closed_by=by,
            closed_at=item.created_at,
            item_outcome=GardenProposalItemOutcome.MINTED,
            source=item.source,
            ref=item.ref,
        )


@dto
@dataclass(frozen=True)
class MintingAccept:
    """What an accept that mints an item writes: the item's title and composed body, and
    the closure's normalized reason."""

    proposal_id: str
    title: str
    body: str
    reason: str | None

    @classmethod
    def of(
        cls,
        proposal: GardenProposal,
        existing: GardenProposalClosure | None,
        *,
        reason: str | None,
        body: str | None,
        findings: Sequence[Finding],
    ) -> MintingAccept:
        """Refuses a closed proposal; otherwise the item takes `body` when given, else the
        proposal's own, wrapped with `findings` (:func:`_compose_minted_body`)."""
        proposal.require_legal(GardenProposalVerb.ACCEPT, existing)
        return cls(
            proposal_id=proposal.proposal_id,
            title=proposal.title,
            body=_compose_minted_body(body if body is not None else proposal.body, findings),
            reason=accept_reason(reason),
        )


def accept_reason(reason: str | None) -> str | None:
    """An accept's optional reason, stripped; a blank one reads as no reason."""
    if reason is None:
        return None
    return reason.strip() or None


@dto
@dataclass(frozen=True)
class AcceptedGardenProposal:
    """The result of accepting a garden proposal — the closure record, plus the minted
    item's chunk id when acceptance minted one."""

    closure: GardenProposalClosure
    chunk_id: str | None


class GardenProposalBodyWithoutMint(ValueError):
    """An accept declining to mint named a `body` override — the override is the minted
    item's body, so an accept minting nothing can never carry it."""

    def __init__(self, proposal_id: str) -> None:
        super().__init__(f"accepting garden proposal {proposal_id} without minting takes no body")
        self.proposal_id = proposal_id


class GardenProposalPassReasonRequired(ValueError):
    """A pass named no reason — passing wants one more than accepting does, since it is
    the note that stops a later run raising the same response as though it were new."""

    def __init__(self) -> None:
        super().__init__("passing a garden proposal requires a reason")


# --- Repository seams (I-prefix, read/write split — bzh:repository-split) ----


class IReadGardenProposalClosureRepository(Protocol):
    """Read-only garden-proposal-closure access."""

    def get(self, proposal_id: str) -> GardenProposalClosure | None: ...

    def get_many(self, proposal_ids: Sequence[str]) -> dict[str, GardenProposalClosure]:
        """Every closure among `proposal_ids`, keyed by `proposal_id` — a proposal with
        no closure is simply absent. One batch read, for a list view's fan-out."""
        ...

    def find_by_item(self, source: str, ref: str) -> GardenProposalClosure | None:
        """The accepted closure that minted `(source, ref)`'s item, or `None` when no
        closure names that pointer — a pass or a declined accept, or simply no closure at
        all (reaching the proposal a delivered item answers)."""
        ...


class IWriteGardenProposalClosureRepository(IReadGardenProposalClosureRepository, Protocol):
    """Read-write garden-proposal-closure access — the pass and accept-declining-to-mint
    writes. The accept-with-mint write lives on
    :class:`~blizzard.hub.domain.chunk.model.IWriteWorkItemRepository` instead: only the item's
    own adapter can enclose the item and chunk inserts in that transaction."""

    def record_pass(self, proposal_id: str, *, reason: str, closed_by: str, at: datetime) -> bool:
        """Insert the passed closure row. Returns ``False`` and writes nothing when
        `proposal_id` already carries a closure."""
        ...

    def record_accept_decline(self, proposal_id: str, *, reason: str | None, closed_by: str, at: datetime) -> bool:
        """Insert the accepted-declining-to-mint closure row. Returns ``False`` and
        writes nothing when `proposal_id` already carries a closure."""
        ...


def _compose_minted_body(body: str, findings: Sequence[Finding]) -> str:
    """Wrap ``body`` with a "Related findings" section — one snapshot bullet per
    ``findings`` entry plus the two lease-scoped read verbs. Empty
    ``findings`` returns ``body`` unchanged — no section, no preamble."""
    if not findings:
        return body
    bullets = "\n".join(f"- `{f.finding_id}` — {f.class_} — {f.locus} — {f.state}" for f in findings)
    return (
        f"{body}\n\n"
        "## Related findings\n\n"
        "These are the findings this work item answers, with each one's state as of this "
        "accept — read them for their current state with `blizzard runner finding list`, or "
        "one in full with `blizzard runner finding get <finding-id>`.\n\n"
        f"{bullets}"
    )


class GardenProposalClosureService:
    """Close a garden proposal — pass or accept — over the closure write seam,
    :class:`~blizzard.hub.domain.work_items.editing.WorkItemEditService` (an accept's mint-with-link path),
    and `default_graph`. Every refusal is :class:`GardenProposalClosure`'s own; this service loads, reads the
    clock, writes, and turns a lost write race into :class:`GardenProposalAlreadyClosed`."""

    def __init__(
        self,
        *,
        closures: IWriteGardenProposalClosureRepository,
        items: WorkItemEditService,
        default_graph: Callable[[], Graph],
        clock: IClock,
    ) -> None:
        self._closures = closures
        self._items = items
        self._default_graph = default_graph
        self._clock = clock

    def pass_(self, proposal: GardenProposal, *, reason: str, by: str) -> GardenProposalClosure:
        """Pass ``proposal`` (:meth:`GardenProposalClosure.passing`)."""
        closure = GardenProposalClosure.passing(
            proposal, self._closures.get(proposal.proposal_id), reason=reason, by=by, at=self._clock.now()
        )
        assert closure.reason is not None
        if not self._closures.record_pass(
            proposal.proposal_id, reason=closure.reason, closed_by=by, at=closure.closed_at
        ):
            self._raise_already_closed(proposal.proposal_id)
        return closure

    def accept(
        self,
        proposal: GardenProposal,
        *,
        reason: str | None,
        by: str,
        body: str | None,
        mint: bool,
        findings: Sequence[Finding],
    ) -> AcceptedGardenProposal:
        """Accept ``proposal`` — ``blizzard-context:/domain/findings-and-proposals.md`` §Closing a proposal.
        ``mint=True`` resolves the default graph only once the proposal is known open and composes the item
        body from the already-loaded ``findings``. Raises :class:`GardenProposalAlreadyClosed`,
        :class:`GardenProposalBodyWithoutMint` for a body on a non-minting accept, and
        :class:`~blizzard.hub.domain.chunk.ingest.IngestConflict` on a raced ref."""
        existing = self._closures.get(proposal.proposal_id)
        if not mint:
            closure = GardenProposalClosure.accepted_declining(
                proposal, existing, reason=reason, body=body, by=by, at=self._clock.now()
            )
            if not self._closures.record_accept_decline(
                proposal.proposal_id, reason=closure.reason, closed_by=by, at=closure.closed_at
            ):
                self._raise_already_closed(proposal.proposal_id)
            return AcceptedGardenProposal(closure=closure, chunk_id=None)
        accept = MintingAccept.of(proposal, existing, reason=reason, body=body, findings=findings)
        minted = self._items.accept_create(
            proposal.proposal_id,
            title=accept.title,
            body=accept.body,
            author=WorkItemAuthor.user(by),
            graph=self._default_graph(),
            reason=accept.reason,
            closed_by=by,
        )
        if minted is None:
            self._raise_already_closed(proposal.proposal_id)
        return AcceptedGardenProposal(
            closure=GardenProposalClosure.accepted_minting(accept, minted.item, by=by), chunk_id=minted.chunk_id
        )

    def _raise_already_closed(self, proposal_id: str) -> NoReturn:
        closure = self._closures.get(proposal_id)
        assert closure is not None
        raise GardenProposalAlreadyClosed(proposal_id, closure)
