"""Finding wire shapes — the candidate, the delta ops, and the read view.

Both this and ``blizzard.wire.garden_proposal`` are the platform's own shapes
(blizzard-product:/delivered/garden/machinery.md §Where the formats live): a garden graph
never carries its own copy."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import AfterValidator, BaseModel, ConfigDict, Field

from blizzard.foundation.findings import FindingExit, FindingFactKind, FindingSeverity, FindingSource, FindingState


def _require_text(value: str) -> str:
    if not value.strip():
        raise ValueError("must not be blank")
    return value


# A finding's class, locus, and summary are free text, but never empty: a blank one mints a finding no one can read.
NonBlankText = Annotated[str, AfterValidator(_require_text)]


class FindingCandidate(BaseModel):
    """A run's survey artifact entry — no id, since identity is minted at delivery (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md)).
    `ref` is stable only within its own submission, so a later node in the same run can
    name it."""

    model_config = ConfigDict(populate_by_name=True, json_schema_serialization_defaults_required=True)

    ref: str | None = None
    class_: NonBlankText = Field(alias="class")
    locus: NonBlankText
    summary: NonBlankText
    introduced: str | None = None


class AddFindingOp(BaseModel):
    """The candidate minus its identity — a delta, not a state (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md))
    — the hub mints the `fin_` id, never the run. Optional `ref` names this addition
    within its own submission, for a proposal in the same delivery to cite."""

    model_config = ConfigDict(populate_by_name=True, json_schema_serialization_defaults_required=True)

    op: Literal["add"] = "add"
    class_: NonBlankText = Field(alias="class")
    locus: NonBlankText
    summary: NonBlankText
    introduced: str | None = None
    ref: str | None = None


class ObservedFindingOp(BaseModel):
    """The finding named by `id` still reproduces — no payload, since it was true when
    recorded and is true now."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    op: Literal["observed"] = "observed"
    id: str


class GoneFindingOp(BaseModel):
    """The run looked and could not find the finding named by `id`. Ordinarily this does
    not close the finding — it flags it for a person — except against a `delivered`
    finding, which it settles to `resolved` outright: a delivery
    already carries a person's own claim that the ground moved."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    op: Literal["gone"] = "gone"
    id: str
    note: str


FindingOp = Annotated[AddFindingOp | ObservedFindingOp | GoneFindingOp, Field(discriminator="op")]


class FindingDelta(BaseModel):
    """A delivered finding list — the scope, the revision read per repository, and the
    routine's measurement, properties of the artifact rather than of any one finding (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md))."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    scope: str
    revisions: dict[str, str] = {}
    measurement: str | None = None
    findings: list[FindingOp] = []


class FindingSurvey(BaseModel):
    """A run's survey artifact — the scope, the revision read per repository, the
    routine's measurement, and every `FindingCandidate` the run saw."""

    model_config = ConfigDict(json_schema_serialization_defaults_required=True)

    scope: str
    revisions: dict[str, str] = {}
    measurement: str | None = None
    candidates: list[FindingCandidate]


class DeferredReviewFindingEntry(BaseModel):
    """A `deferred` entry — a still-open should-fix finding a passing
    review leaves unanswered, the only disposition that mints. Carries the
    `garden/finding-format` `AddFindingOp` fields plus `severity`, all required."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ref: str
    disposition: Literal["deferred"] = "deferred"
    severity: FindingSeverity
    scope: str
    class_: NonBlankText = Field(alias="class")
    locus: NonBlankText
    summary: NonBlankText


class FixedReviewFindingEntry(BaseModel):
    """A `fixed` entry — the review already settled it; only `ref` is read."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    disposition: Literal["fixed"] = "fixed"


class RefutedReviewFindingEntry(BaseModel):
    """A `refuted` entry — the review already settled it; only `ref` is read."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    disposition: Literal["refuted"] = "refuted"


ReviewFindingEntry = Annotated[
    DeferredReviewFindingEntry | FixedReviewFindingEntry | RefutedReviewFindingEntry,
    Field(discriminator="disposition"),
]


class ReviewFindingDelta(BaseModel):
    """A delivery lane review round's own delta — the wire shape
    `review/finding-format` documents in full; this restates only the field meanings a
    caller needs. `entries` is required, not defaulted: a payload naming no `entries`
    key at all is refused rather than read as an empty, `recorded` delta."""

    model_config = ConfigDict(extra="forbid")

    entries: list[ReviewFindingEntry]


class FindingView(BaseModel):
    """A finding. `state` folds the newest fact's kind to `"live"` for
    `add`/`observed`/`reopened`; `note` is that fact's own note. `source`
    is `"routine"` or `"review"`: a review-sourced finding carries no
    `routine_name`, its own `severity`, and the `raised_by_chunk_id` that raised it."""

    model_config = ConfigDict(populate_by_name=True)

    finding_id: str
    routine_name: str | None = None
    scope_slug: str
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None
    introduced_at: str | None = None
    first_observed_at: str | None = None
    live: bool
    state: FindingState
    note: str | None = None
    last_seen_at: str | None
    observed_count: int
    source: FindingSource = FindingSource.ROUTINE
    severity: FindingSeverity | None = None
    raised_by_chunk_id: str | None = None
    #: How an exited finding left; `None` while it has not exited.
    exit: FindingExit | None = None


class FindingsPageView(BaseModel):
    """``GET /api/findings``'s own bounded page — ``next_cursor`` is
    ``None`` exactly when this page is the last one."""

    findings: list[FindingView] = []
    next_cursor: str | None = None


class FindingFactView(BaseModel):
    """One entry in a finding's fact chain, oldest-first."""

    kind: FindingFactKind
    recorded_at: str
    note: str | None = None
    actor: str | None = None
    proposal_id: str | None = None
    superseded_by: str | None = None


class FindingDetailView(FindingView):
    """`GET /api/findings/{finding_id}`'s own response model — adds the
    finding's whole fact chain, oldest-first, atop every `FindingView` field. The list
    read (`GET /api/findings`) returns plain `FindingView` and carries no chain."""

    facts: list[FindingFactView]


class FindingExitRequest(BaseModel):
    """`POST /api/findings/{verb}` — the shared shape for every human-driven exit and
    `reopen` except `supersede`: every finding named exits (or
    reopens) together, one call, carrying the same required note."""

    model_config = ConfigDict(extra="forbid")

    finding_ids: list[str] = Field(min_length=1)
    note: str


class FindingSupersedeRequest(FindingExitRequest):
    """`POST /api/findings/supersede` — `FindingExitRequest` plus the absorbing finding."""

    superseded_by: str
