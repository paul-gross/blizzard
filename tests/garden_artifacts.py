"""Garden artifact builders for the garden tests: a payload parsed through the boundary's own
:class:`~blizzard.hub.api.garden_formats.GardenFormats` into the domain's models, and a domain model
rendered back to the raw artifact JSON a delivery reads."""

from __future__ import annotations

import json
from collections.abc import Mapping, Sequence

from blizzard.hub.api.garden_formats import GardenFormats
from blizzard.hub.domain.garden.formats import (
    DeferredReviewEntry,
    DeliveredDelta,
    FindingAddOp,
    FindingGoneOp,
    FindingObservedOp,
    ProposalCandidate,
)
from blizzard.wire.finding import AddFindingOp, FindingDelta, GoneFindingOp, ObservedFindingOp
from blizzard.wire.garden_proposal import GardenProposalCandidate

_FORMATS = GardenFormats()


def delivered_delta(payload: Mapping[str, object]) -> DeliveredDelta:
    return _FORMATS.finding_delta("test-delta", json.dumps(payload))


def add_op(payload: Mapping[str, object]) -> FindingAddOp:
    op = delivered_delta({"scope": "test", "findings": [payload]}).findings[0]
    assert isinstance(op, FindingAddOp)
    return op


def candidate(payload: Mapping[str, object]) -> ProposalCandidate:
    return _FORMATS.proposal_candidates("test-proposals", json.dumps([payload]))[0]


def wire_json(model: DeliveredDelta | ProposalCandidate) -> str:
    """``model`` as the JSON a worker writes into its artifact."""
    if isinstance(model, ProposalCandidate):
        return _wire_candidate(model).model_dump_json(by_alias=True)
    return FindingDelta(
        scope=model.scope,
        revisions=dict(model.revisions),
        measurement=model.measurement,
        findings=[_wire_op(op) for op in model.findings],
    ).model_dump_json(by_alias=True)


def candidates_json(models: Sequence[ProposalCandidate]) -> str:
    return "[" + ",".join(wire_json(m) for m in models) + "]"


def _wire_candidate(model: ProposalCandidate) -> GardenProposalCandidate:
    return GardenProposalCandidate.model_validate(
        {"ref": model.ref, "class": model.class_, "title": model.title, "body": model.body, "findings": model.findings}
    )


def _wire_op(op: FindingAddOp | FindingObservedOp | FindingGoneOp) -> AddFindingOp | ObservedFindingOp | GoneFindingOp:
    if isinstance(op, FindingAddOp):
        return AddFindingOp.model_validate(
            {"class": op.class_, "locus": op.locus, "summary": op.summary, "introduced": op.introduced, "ref": op.ref}
        )
    if isinstance(op, FindingObservedOp):
        return ObservedFindingOp(id=op.id)
    return GoneFindingOp(id=op.id, note=op.note)


def deferred_entry(payload: Mapping[str, object]) -> DeferredReviewEntry:
    entry = _FORMATS.review_delta("test-review", json.dumps({"entries": [payload]})).entries[0]
    assert isinstance(entry, DeferredReviewEntry)
    return entry
