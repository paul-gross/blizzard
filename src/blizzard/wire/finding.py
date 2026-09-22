"""Finding wire shapes (blizzard#390) — the candidate, the delta ops, and the read view.

Both this and ``blizzard.wire.garden_proposal`` are the platform's own shapes
(blizzard-product:/delivered/garden/machinery.md §Where the formats live): a garden graph
never carries its own copy."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FindingCandidate(BaseModel):
    """A run's survey artifact entry — no id, since identity is minted at delivery (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md)).
    `ref` is stable only within its own submission, so a later node in the same run can
    name it."""

    model_config = ConfigDict(populate_by_name=True)

    ref: str
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None


class AddFindingOp(BaseModel):
    """The candidate minus its identity — a delta, not a state (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md))
    — the hub mints the `fin_` id, never the run. Optional `ref` names this addition
    within its own submission, for a proposal in the same delivery to cite."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["add"] = "add"
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None
    ref: str | None = None


class ObservedFindingOp(BaseModel):
    """The finding named by `id` still reproduces — no payload, since it was true when
    recorded and is true now."""

    op: Literal["observed"] = "observed"
    id: str


class GoneFindingOp(BaseModel):
    """The run looked and could not find the finding named by `id`. Ordinarily this does
    not close the finding (D3) — it flags it for a person — except against a `delivered`
    finding, which it settles to `resolved` outright (blizzard#583 D3): a delivery
    already carries a person's own claim that the ground moved."""

    op: Literal["gone"] = "gone"
    id: str
    note: str


FindingOp = Annotated[AddFindingOp | ObservedFindingOp | GoneFindingOp, Field(discriminator="op")]


class FindingDelta(BaseModel):
    """A delivered finding list — the scope, the revision read per repository, and the
    routine's measurement, properties of the artifact rather than of any one finding (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md))."""

    scope: str
    revisions: dict[str, str] = {}
    measurement: str | None = None
    findings: list[FindingOp] = []


class DeferredReviewFindingEntry(BaseModel):
    """A `deferred` entry (blizzard#582 D7) — a still-open should-fix finding a passing
    review leaves unanswered, the only disposition that mints. Carries the
    `garden/finding-format` `AddFindingOp` fields plus `severity`, all required:
    pydantic refuses a delta missing one, rather than a hand-rolled check downstream."""

    model_config = ConfigDict(populate_by_name=True, extra="forbid")

    ref: str
    disposition: Literal["deferred"] = "deferred"
    severity: Literal["blocking", "should-fix"]
    scope: str
    class_: str = Field(alias="class")
    locus: str
    summary: str


class FixedReviewFindingEntry(BaseModel):
    """A `fixed` entry (blizzard#582 D7) — the review already settled it; materialization
    mints nothing further and reads no field beyond `ref`."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    disposition: Literal["fixed"] = "fixed"


class RefutedReviewFindingEntry(BaseModel):
    """A `refuted` entry (blizzard#582 D7) — the review already settled it; materialization
    mints nothing further and reads no field beyond `ref`."""

    model_config = ConfigDict(extra="forbid")

    ref: str
    disposition: Literal["refuted"] = "refuted"


ReviewFindingEntry = Annotated[
    DeferredReviewFindingEntry | FixedReviewFindingEntry | RefutedReviewFindingEntry,
    Field(discriminator="disposition"),
]


class ReviewFindingDelta(BaseModel):
    """A delivery lane review round's own delta (blizzard#582 D7) — the wire shape
    `review/finding-format` documents in full; this restates only the field meanings a
    caller needs. `entries` is required, not defaulted: a payload naming no `entries`
    key at all is refused rather than read as an empty, `recorded` delta."""

    model_config = ConfigDict(extra="forbid")

    entries: list[ReviewFindingEntry]


class FindingView(BaseModel):
    """A finding. `state` folds the newest fact's kind to `"live"` for
    `add`/`observed`/`reopened` (blizzard#394); `note` is that fact's own note. `source`
    is `"routine"` or `"review"` (blizzard#582 D1): a review-sourced finding carries no
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
    state: str
    note: str | None = None
    last_seen_at: str | None
    observed_count: int
    source: str = "routine"
    severity: str | None = None
    raised_by_chunk_id: str | None = None


class FindingsPageView(BaseModel):
    """``GET /api/findings``'s own bounded page (blizzard#526 D3/D5) — ``next_cursor`` is
    ``None`` exactly when this page is the last one."""

    findings: list[FindingView] = []
    next_cursor: str | None = None


class FindingFactView(BaseModel):
    """One entry in a finding's fact chain, oldest-first — `FindingFact` on the wire
    (blizzard#487)."""

    kind: str
    recorded_at: str
    note: str | None = None
    actor: str | None = None
    proposal_id: str | None = None
    superseded_by: str | None = None


class FindingDetailView(FindingView):
    """`GET /api/findings/{finding_id}`'s own response model (blizzard#487) — adds the
    finding's whole fact chain, oldest-first, atop every `FindingView` field. The list
    read (`GET /api/findings`) returns plain `FindingView` and carries no chain."""

    facts: list[FindingFactView]


class FindingExitRequest(BaseModel):
    """`POST /api/findings/{verb}` — the shared shape for every human-driven exit and
    `reopen` except `supersede` (blizzard#394): every finding named exits (or
    reopens) together, one call, carrying the same required note (D7)."""

    model_config = ConfigDict(extra="forbid")

    finding_ids: list[str] = Field(min_length=1)
    note: str


class FindingSupersedeRequest(FindingExitRequest):
    """`POST /api/findings/supersede` — `FindingExitRequest` plus the absorbing finding
    (D4)."""

    superseded_by: str
