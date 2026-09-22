"""Review-finding delivery validation (blizzard#582) — the `record-findings` node's own
shape check, before anything is written. Pure functions over already-parsed objects
(`bzh:domain-takes-objects`), no I/O. Materializing a passing result is
`review_findings_materialize.py`'s, mirroring the garden delivery split
(`garden_delivery.py`/`garden_delivery_materialize.py`)."""

from __future__ import annotations

from dataclasses import dataclass, field

from pydantic import ValidationError

from blizzard.hub.domain.scopes import ScopeSlug, ScopeSlugError
from blizzard.wire.finding import ReviewFindingDelta, ReviewFindingEntry


class ReviewFindingsRejected(Exception):
    """A delivered review-finding delta failed validation. This exception's own message
    is the operator-legible reason the graph attaches to its bounce — never a raw
    pydantic error."""


@dataclass(frozen=True)
class ValidatedReviewFindings:
    """What a passing :func:`validate_review_findings` hands the materializer: only the
    `deferred` entries, which alone mint (`fixed`/`refuted` carry nothing further)."""

    deferred: list[ReviewFindingEntry] = field(default_factory=list)


def parse_review_finding_delta(artifact_name: str, raw: str) -> ReviewFindingDelta:
    """Parse `raw` as JSON and validate it against :class:`ReviewFindingDelta`. Both a
    JSON syntax failure and a shape mismatch surface as pydantic `ValidationError` from
    `model_validate_json` and become one :class:`ReviewFindingsRejected`, naming
    `artifact_name` rather than dumping the raw pydantic error."""
    try:
        return ReviewFindingDelta.model_validate_json(raw)
    except ValidationError as exc:
        raise ReviewFindingsRejected(
            f"artifact {artifact_name!r} does not match the review-finding-delta shape: {_summarize(exc)}"
        ) from exc


def validate_review_findings(delta: ReviewFindingDelta) -> ValidatedReviewFindings:
    """Validate `delta`, raising :class:`ReviewFindingsRejected` on the first failure: a
    duplicate `ref`, a `deferred` entry missing one of its required fields, a `deferred`
    entry marked `blocking` (a passing review cannot hold one), or a malformed scope
    slug. Returns only the `deferred` entries — `fixed`/`refuted` mint nothing."""
    seen_refs: set[str] = set()
    deferred: list[ReviewFindingEntry] = []
    for entry in delta.entries:
        if entry.ref in seen_refs:
            raise ReviewFindingsRejected(f"ref {entry.ref!r} is carried by more than one entry in this delta")
        seen_refs.add(entry.ref)
        if entry.disposition != "deferred":
            continue
        missing = [
            name
            for name, value in (
                ("severity", entry.severity),
                ("scope", entry.scope),
                ("class", entry.class_),
                ("locus", entry.locus),
                ("summary", entry.summary),
            )
            if value is None
        ]
        if missing:
            raise ReviewFindingsRejected(
                f"entry {entry.ref!r} is deferred but is missing required field(s): {', '.join(missing)}"
            )
        if entry.severity == "blocking":
            raise ReviewFindingsRejected(
                f"entry {entry.ref!r} is deferred and marked blocking — a passing review cannot hold one"
            )
        assert entry.scope is not None  # narrowed by the missing-field check above
        try:
            ScopeSlug.parse(entry.scope)
        except ScopeSlugError as exc:
            raise ReviewFindingsRejected(f"entry {entry.ref!r} names a malformed scope slug: {exc}") from exc
        deferred.append(entry)
    return ValidatedReviewFindings(deferred=deferred)


def _summarize(exc: ValidationError) -> str:
    """A short, operator-legible rendering of `exc` — location and message per error,
    never pydantic's own multi-line `str()` with its "further information" links."""
    parts = []
    for error in exc.errors():
        loc = ".".join(str(part) for part in error["loc"]) or "<root>"
        parts.append(f"{loc}: {error['msg']}")
    return "; ".join(parts)
