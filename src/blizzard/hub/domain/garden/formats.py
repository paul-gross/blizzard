"""The garden formats — a run's finding delta, its proposal candidates, and a review round's
finding delta — as the garden domain reads them, and the port that parses one from raw JSON;
a parse failure raises the domain's own rejection."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass, field
from typing import Protocol

from blizzard.foundation.findings import FindingSeverity
from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class FindingAddOp:
    """A finding the run saw that no existing one covers — its class, locus, and summary, the
    commit it was ``introduced`` in, and a ``ref`` a proposal in the same delivery may cite.
    The hub mints the ``fin_`` id."""

    class_: str
    locus: str
    summary: str
    introduced: str | None = None
    ref: str | None = None


@domain_model
@dataclass(frozen=True)
class FindingObservedOp:
    """The finding named by ``id`` still reproduces."""

    id: str


@domain_model
@dataclass(frozen=True)
class FindingGoneOp:
    """The run looked and could not find the finding named by ``id``; ``note`` says why."""

    id: str
    note: str


type FindingOp = FindingAddOp | FindingObservedOp | FindingGoneOp


@domain_model
@dataclass(frozen=True)
class DeliveredDelta:
    """A run's delivered finding list — its scope, the revision it read per repository, the
    routine's measurement, and one op per finding it added, re-observed, or found gone."""

    scope: str
    revisions: Mapping[str, str] = field(default_factory=dict)
    measurement: str | None = None
    findings: list[FindingOp] = field(default_factory=list)


@domain_model
@dataclass(frozen=True)
class ProposalCandidate:
    """A run's proposed response — its submission-local ``ref``, class, title, body, and the
    findings it answers, each a ``fin_`` id or an add op's ``ref``."""

    ref: str
    class_: str
    title: str
    body: str
    findings: list[str] = field(default_factory=list)


@domain_model
@dataclass(frozen=True)
class DeferredReviewEntry:
    """A still-open should-fix finding a passing review leaves unanswered — the only review
    disposition that mints a finding."""

    ref: str
    severity: FindingSeverity
    scope: str
    class_: str
    locus: str
    summary: str


@domain_model
@dataclass(frozen=True)
class SettledReviewEntry:
    """A review entry the review already settled, ``fixed`` or ``refuted`` — only its ``ref``
    is read."""

    ref: str


type ReviewEntry = DeferredReviewEntry | SettledReviewEntry


@domain_model
@dataclass(frozen=True)
class ReviewDelta:
    """A delivery lane review round's own finding delta."""

    entries: list[ReviewEntry]


class IGardenFormats(Protocol):
    """Parses a garden artifact's raw JSON into its domain model. A JSON or shape failure raises
    the domain's own rejection, naming ``artifact_name`` and an operator-legible reason."""

    def finding_delta(self, artifact_name: str, raw: str) -> DeliveredDelta:
        """Raises :class:`~blizzard.hub.domain.garden.delivery.validation.GardenDeliveryRejected`."""
        ...

    def proposal_candidates(self, artifact_name: str, raw: str) -> list[ProposalCandidate]:
        """Raises :class:`~blizzard.hub.domain.garden.delivery.validation.GardenDeliveryRejected`."""
        ...

    def review_delta(self, artifact_name: str, raw: str) -> ReviewDelta:
        """Raises :class:`~blizzard.hub.domain.garden.review.validation.ReviewFindingsRejected`."""
        ...
