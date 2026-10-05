"""The garden artifact formats parsed at the app boundary: the published pydantic shapes validate
an artifact's raw JSON and the result maps to the garden domain's own models
(:class:`~blizzard.hub.domain.garden.formats.IGardenFormats`, ``bzh:data-roles``)."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import TypeAdapter, ValidationError

from blizzard.foundation.roles import collaborator
from blizzard.hub.domain.garden.delivery.validation import GardenDeliveryRejected
from blizzard.hub.domain.garden.formats import (
    DeferredReviewEntry,
    DeliveredDelta,
    FindingAddOp,
    FindingGoneOp,
    FindingObservedOp,
    FindingOp,
    ProposalCandidate,
    ReviewDelta,
    ReviewEntry,
    SettledReviewEntry,
)
from blizzard.hub.domain.garden.review.validation import ReviewFindingsRejected
from blizzard.wire.finding import (
    AddFindingOp,
    DeferredReviewFindingEntry,
    FindingDelta,
    FixedReviewFindingEntry,
    GoneFindingOp,
    ObservedFindingOp,
    RefutedReviewFindingEntry,
    ReviewFindingDelta,
)
from blizzard.wire.garden_proposal import GardenProposalCandidate

_PROPOSALS_ADAPTER: TypeAdapter[list[GardenProposalCandidate]] = TypeAdapter(list[GardenProposalCandidate])


@collaborator
@dataclass(frozen=True)
class GardenFormats:
    """Validates each garden artifact against its published shape. A JSON syntax failure and a
    shape mismatch alike become the domain's one rejection, naming the artifact rather than
    dumping pydantic's own error."""

    def finding_delta(self, artifact_name: str, raw: str) -> DeliveredDelta:
        try:
            wire = FindingDelta.model_validate_json(raw)
        except ValidationError as exc:
            raise GardenDeliveryRejected(
                f"artifact {artifact_name!r} does not match the finding-delta shape: {_summarize(exc)}"
            ) from exc
        return DeliveredDelta(
            scope=wire.scope,
            revisions=dict(wire.revisions),
            measurement=wire.measurement,
            findings=[_finding_op(op) for op in wire.findings],
        )

    def proposal_candidates(self, artifact_name: str, raw: str) -> list[ProposalCandidate]:
        try:
            wire = _PROPOSALS_ADAPTER.validate_json(raw)
        except ValidationError as exc:
            raise GardenDeliveryRejected(
                f"artifact {artifact_name!r} does not match the garden-proposal-candidate shape: {_summarize(exc)}"
            ) from exc
        return [
            ProposalCandidate(ref=c.ref, class_=c.class_, title=c.title, body=c.body, findings=list(c.findings))
            for c in wire
        ]

    def review_delta(self, artifact_name: str, raw: str) -> ReviewDelta:
        try:
            wire = ReviewFindingDelta.model_validate_json(raw)
        except ValidationError as exc:
            raise ReviewFindingsRejected(
                f"artifact {artifact_name!r} does not match the review-finding-delta shape: {_summarize(exc)}"
            ) from exc
        return ReviewDelta(entries=[_review_entry(entry) for entry in wire.entries])


def _finding_op(op: AddFindingOp | ObservedFindingOp | GoneFindingOp) -> FindingOp:
    if isinstance(op, AddFindingOp):
        return FindingAddOp(class_=op.class_, locus=op.locus, summary=op.summary, introduced=op.introduced, ref=op.ref)
    if isinstance(op, ObservedFindingOp):
        return FindingObservedOp(id=op.id)
    return FindingGoneOp(id=op.id, note=op.note)


def _review_entry(
    entry: DeferredReviewFindingEntry | FixedReviewFindingEntry | RefutedReviewFindingEntry,
) -> ReviewEntry:
    if isinstance(entry, DeferredReviewFindingEntry):
        return DeferredReviewEntry(
            ref=entry.ref,
            severity=entry.severity,
            scope=entry.scope,
            class_=entry.class_,
            locus=entry.locus,
            summary=entry.summary,
        )
    return SettledReviewEntry(ref=entry.ref)


def _summarize(exc: ValidationError) -> str:
    """A short, operator-legible rendering of `exc` — location and message per error,
    never pydantic's own multi-line `str()` with its "further information" links."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
