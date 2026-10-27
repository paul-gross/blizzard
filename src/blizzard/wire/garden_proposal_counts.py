"""Garden-proposal counts read views — one counts row and the counts response."""

from __future__ import annotations

from typing import Annotated

from pydantic import ConfigDict, Field

from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class GardenProposalCountsRowView(WireModel):
    """One origin/routine/class triple's garden-proposal counts over the requested
    window. `routine_name` is nullable for an operator-authored row
    naming no routine."""

    model_config = ConfigDict(populate_by_name=True)

    origin: Annotated[GardenProposalOrigin | Unrecognized, OPEN]
    routine_name: str | None
    class_: str = Field(alias="class")
    open: int
    passed: int
    accepted_with_item: int
    accepted_without_item: int
    #: The sum of the four bucket counts.
    created: int
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None


class GardenProposalCountsView(WireModel):
    """`GET /api/routines/proposal-counts`'s own response — `routine` echoes the
    optional routine filter, `origin` the optional origin filter, each `None` when
    unfiltered."""

    since: str
    until: str
    routine: str | None
    origin: Annotated[GardenProposalOrigin | Unrecognized, OPEN] | None = None
    rows: list[GardenProposalCountsRowView]
