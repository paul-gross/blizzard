"""Finding wire shapes (blizzard#390) — the candidate, the delta ops, and the read view.

Both this and ``blizzard.wire.garden_proposal`` are the platform's own shapes
(blizzard-product:/plans/garden/machinery.md §Where the formats live): a garden graph
never carries its own copy. Nothing in production writes these yet — delivery is a
sibling issue; this is the format it will validate against."""

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
    """The candidate minus its `ref` — a delta, not a state (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md))
    — the hub mints an id for each."""

    model_config = ConfigDict(populate_by_name=True)

    op: Literal["add"] = "add"
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None


class ObservedFindingOp(BaseModel):
    """The finding named by `id` still reproduces — no payload, since it was true when
    recorded and is true now."""

    op: Literal["observed"] = "observed"
    id: str


class GoneFindingOp(BaseModel):
    """The run looked and could not find the finding named by `id`. Does not close the
    finding (D3) — it flags it for a person."""

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


class FindingView(BaseModel):
    """A finding."""

    model_config = ConfigDict(populate_by_name=True)

    finding_id: str
    routine_name: str
    scope_slug: str
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None
    live: bool
    last_seen_at: str | None
    observed_count: int
