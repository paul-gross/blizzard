"""Garden-proposal closure — the two verbs that end a proposal's life:
**pass** (considered and declined, with a reason) and **accept** (agreed, minting a
linked hub work item by default). Closure is terminal, mirroring
:class:`~blizzard.foundation.work_items.WorkItemClosure`. Takes an already-loaded
:class:`~blizzard.hub.domain.garden_proposals.GardenProposal` (``bzh:domain-takes-objects``)."""

from __future__ import annotations

from collections.abc import Sequence
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import NoReturn, Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.garden_proposals import GardenProposalClosureKind, GardenProposalItemOutcome
from blizzard.foundation.roles import domain_model, dto
from blizzard.hub.domain.findings import Finding
from blizzard.hub.domain.garden_proposals import GardenProposal, GardenProposalAlreadyClosed
from blizzard.hub.domain.graph import Graph
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.domain.work_items import WorkItemEditService


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


@dto
@dataclass(frozen=True)
class AcceptedGardenProposal:
    """The result of accepting a garden proposal — the closure record, plus the minted
    item's chunk id when acceptance minted one."""

    closure: GardenProposalClosure
    chunk_id: str | None


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
    :class:`~blizzard.hub.domain.work.IWriteWorkItemRepository` instead: only the item's
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
    """Close a garden proposal — pass or accept. Holds the closure write seam and
    :class:`~blizzard.hub.domain.work_items.WorkItemEditService`, the mint-with-link
    path an accept that mints rides."""

    def __init__(
        self, *, closures: IWriteGardenProposalClosureRepository, items: WorkItemEditService, clock: IClock
    ) -> None:
        self._closures = closures
        self._items = items
        self._clock = clock

    def pass_(self, proposal: GardenProposal, *, reason: str, by: str) -> GardenProposalClosure:
        """Pass ``proposal`` with ``reason``, refusing a blank one. Raises
        :class:`GardenProposalAlreadyClosed` when it already carries a closure, checked
        against ``self._closures.get`` here and re-checked by the store's own
        idempotence guard against a race in between."""
        reason = reason.strip()
        if not reason:
            raise GardenProposalPassReasonRequired()
        self._refuse_if_closed(proposal.proposal_id)
        at = self._clock.now()
        written = self._closures.record_pass(proposal.proposal_id, reason=reason, closed_by=by, at=at)
        if not written:
            self._raise_already_closed(proposal.proposal_id)
        return GardenProposalClosure(
            proposal_id=proposal.proposal_id,
            closure=GardenProposalClosureKind.PASSED,
            reason=reason,
            closed_by=by,
            closed_at=at,
            item_outcome=None,
            source=None,
            ref=None,
        )

    def accept(
        self,
        proposal: GardenProposal,
        *,
        reason: str | None,
        by: str,
        body: str | None,
        mint: bool,
        graph: Graph | None,
        findings: Sequence[Finding],
    ) -> AcceptedGardenProposal:
        """Accept ``proposal`` — see ``blizzard-context:/domain/findings-and-proposals.md``
        §Closing a proposal: pass or accept. ``mint=True`` requires ``graph`` and composes
        the item body (``_compose_minted_body``) from ``findings``, already-loaded objects
        the caller resolves (``bzh:domain-takes-objects``). Raises :class:`GardenProposalAlreadyClosed` when
        already closed, and :class:`~blizzard.hub.domain.ingest.IngestConflict` on a raced ref."""
        self._refuse_if_closed(proposal.proposal_id)
        if not mint:
            at = self._clock.now()
            written = self._closures.record_accept_decline(proposal.proposal_id, reason=reason, closed_by=by, at=at)
            if not written:
                self._raise_already_closed(proposal.proposal_id)
            return AcceptedGardenProposal(
                closure=GardenProposalClosure(
                    proposal_id=proposal.proposal_id,
                    closure=GardenProposalClosureKind.ACCEPTED,
                    reason=reason,
                    closed_by=by,
                    closed_at=at,
                    item_outcome=GardenProposalItemOutcome.DECLINED,
                    source=None,
                    ref=None,
                ),
                chunk_id=None,
            )
        assert graph is not None
        minted = self._items.accept_create(
            proposal.proposal_id,
            title=proposal.title,
            body=_compose_minted_body(body if body is not None else proposal.body, findings),
            author=WorkItemAuthor.user(by),
            graph=graph,
            reason=reason,
            closed_by=by,
        )
        if minted is None:
            self._raise_already_closed(proposal.proposal_id)
        return AcceptedGardenProposal(
            closure=GardenProposalClosure(
                proposal_id=proposal.proposal_id,
                closure=GardenProposalClosureKind.ACCEPTED,
                reason=reason,
                closed_by=by,
                closed_at=minted.item.created_at,
                item_outcome=GardenProposalItemOutcome.MINTED,
                source=minted.item.source,
                ref=minted.item.ref,
            ),
            chunk_id=minted.chunk_id,
        )

    def _refuse_if_closed(self, proposal_id: str) -> None:
        closure = self._closures.get(proposal_id)
        if closure is not None:
            raise GardenProposalAlreadyClosed(proposal_id, closure)

    def _raise_already_closed(self, proposal_id: str) -> NoReturn:
        closure = self._closures.get(proposal_id)
        assert closure is not None
        raise GardenProposalAlreadyClosed(proposal_id, closure)
