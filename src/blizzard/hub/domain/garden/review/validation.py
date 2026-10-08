"""Review-finding delivery validation — the `record-findings` node's own
shape check, before anything is written. Pure functions over already-parsed objects
(`bzh:domain-takes-objects`), no I/O. Materializing a passing result is
`src/blizzard/hub/domain/garden/review/materialize.py`'s, mirroring the garden delivery split
(`src/blizzard/hub/domain/garden/delivery/validation.py`/`src/blizzard/hub/domain/garden/delivery/materialize.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from blizzard.foundation.roles import domain_model
from blizzard.hub.domain.garden.formats import DeferredReviewEntry, ReviewDelta
from blizzard.hub.domain.garden.scopes import ScopeSlug, ScopeSlugError


class ReviewFindingsRejected(Exception):
    """A delivered review-finding delta failed validation. This exception's own message
    is the operator-legible reason the graph attaches to its bounce — never a raw
    pydantic error."""


#: The `review` node's own fixed `produces:` asset name.
REVIEW_FINDING_DELTA_ARTIFACT = "review-finding-delta"


def require_review_delta[A](artifact: A | None, *, chunk_id: str) -> A:
    """`artifact`, the chunk's newest review-finding delta; refuses a delivery whose
    chunk carries none."""
    if artifact is None:
        raise ReviewFindingsRejected(f"no {REVIEW_FINDING_DELTA_ARTIFACT!r} artifact found for chunk {chunk_id}")
    return artifact


@domain_model
@dataclass(frozen=True)
class ValidatedReviewFindings:
    """What a passing :func:`validate_review_findings` hands the materializer: only the
    `deferred` entries, which alone mint (`fixed`/`refuted` carry nothing further)."""

    deferred: list[DeferredReviewEntry] = field(default_factory=list)


def validate_review_findings(delta: ReviewDelta) -> ValidatedReviewFindings:
    """Validate `delta`, raising :class:`ReviewFindingsRejected` on the first failure: a
    duplicate `ref`, a `deferred` entry marked `blocking` (a passing review cannot hold
    one), or a malformed scope slug. A `deferred` entry missing a required field never
    reaches here — the review-finding-delta shape itself refuses to parse one. Returns only the
    `deferred` entries — `fixed`/`refuted` mint nothing."""
    seen_refs: set[str] = set()
    deferred: list[DeferredReviewEntry] = []
    for entry in delta.entries:
        if entry.ref in seen_refs:
            raise ReviewFindingsRejected(f"ref {entry.ref!r} is carried by more than one entry in this delta")
        seen_refs.add(entry.ref)
        if not isinstance(entry, DeferredReviewEntry):
            continue
        if entry.severity == "blocking":
            raise ReviewFindingsRejected(
                f"entry {entry.ref!r} is deferred and marked blocking — a passing review cannot hold one"
            )
        try:
            ScopeSlug.parse(entry.scope)
        except ScopeSlugError as exc:
            raise ReviewFindingsRejected(f"entry {entry.ref!r} names a malformed scope slug: {exc}") from exc
        deferred.append(entry)
    return ValidatedReviewFindings(deferred=deferred)
