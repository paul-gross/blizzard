"""Finding wire shapes (blizzard#390) — the candidate, the delta ops, and the read view.

Both this and ``blizzard.wire.garden_proposal`` are the platform's own shapes
(machinery.md §Where the formats live): a garden graph never carries its own copy, and
the hub validates a submission against exactly this. Nothing in production writes these
yet — delivery is a sibling issue; this is the format it will validate against."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field


class FindingCandidate(BaseModel):
    """A run's survey artifact entry — no id, since identity is minted at delivery
    (machinery.md §Identity is the hub's to assign). `ref` is stable only within its own
    submission, so a later node in the same run can name it."""

    model_config = ConfigDict(populate_by_name=True)

    ref: str
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None = None


class AddFindingOp(BaseModel):
    """The candidate minus its `ref` (machinery.md §A run emits a delta, not a state) —
    the hub mints an id for each."""

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
    """A delivered finding list — the scope it was swept under, the revision the run
    read per repository, and the routine's own measurement, all properties of the
    artifact rather than of any single finding, recorded whether or not `findings` holds
    a single entry (machinery.md §A run emits a delta)."""

    scope: str
    revisions: dict[str, str] = {}
    measurement: str | None = None
    findings: list[FindingOp] = []


class FindingView(BaseModel):
    """A finding as served by the read routes and CLI verbs."""

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
