"""Garden-proposal counts (blizzard#547) — the `GET /api/routines/proposal-counts` read
view. `created` is echoed as the sum of the four bucket counts, never carried as its own
column (`GardenProposalCounts.created`'s own rule)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GardenProposalCountsRowView(BaseModel):
    """One routine/class pair's garden-proposal counts over the requested window."""

    model_config = ConfigDict(populate_by_name=True)

    routine_name: str
    class_: str = Field(alias="class")
    open: int
    passed: int
    accepted_with_item: int
    accepted_without_item: int
    created: int


class GardenProposalCountsView(BaseModel):
    """`GET /api/routines/proposal-counts`'s own response — `routine` echoes the
    optional filter, `None` when unfiltered."""

    since: str
    until: str
    routine: str | None
    rows: list[GardenProposalCountsRowView]
