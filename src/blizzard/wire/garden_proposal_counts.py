"""Garden-proposal counts (blizzard#547) — the `GET /api/routines/proposal-counts` read
view. `created` is echoed as the sum of the four bucket counts, never carried as its own
column (`GardenProposalCounts.created`'s own rule)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from blizzard.hub.domain.garden_proposals import GardenProposalOrigin


class GardenProposalCountsRowView(BaseModel):
    """One origin/routine/class triple's garden-proposal counts over the requested
    window (blizzard#631). `routine_name` is nullable for an operator-authored row
    naming no routine."""

    model_config = ConfigDict(populate_by_name=True)

    origin: GardenProposalOrigin
    routine_name: str | None
    class_: str = Field(alias="class")
    open: int
    passed: int
    accepted_with_item: int
    accepted_without_item: int
    created: int


class GardenProposalCountsView(BaseModel):
    """`GET /api/routines/proposal-counts`'s own response — `routine` echoes the
    optional routine filter, `origin` the optional origin filter, each `None` when
    unfiltered."""

    since: str
    until: str
    routine: str | None
    origin: GardenProposalOrigin | None = None
    rows: list[GardenProposalCountsRowView]
