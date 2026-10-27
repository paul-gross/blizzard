"""A garden routine run's own read views — the run list and one run's own delta."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.foundation.run_mode import RunMode
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


class DeliveredSetView(BaseModel):
    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    #: How many `add` facts this delivery recorded; `0` when it recorded none.
    added_count: int
    #: How many `observed` facts this delivery recorded; `0` when it recorded none.
    observed_count: int
    #: How many `gone` facts this delivery recorded; `0` when it recorded none.
    gone_count: int


class RunEscalationView(BaseModel):
    node_name: str | None
    takeover_command: str
    wrapped_takeover_command: str


class RunRowView(WireModel):
    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: Annotated[RunMode | Unrecognized, OPEN]
    minted_at: str
    outcome: Annotated[ChunkStatus | Unrecognized, OPEN]
    escalation: RunEscalationView | None
    delivered: list[DeliveredSetView]
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None


class AddedFindingView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    finding_id: str | None = Field(
        description="The finding this add minted, or null when the add is linked to no finding."
    )
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None


class ObservedFindingView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    finding_id: str
    #: The observed finding's class; null when the id names no known finding.
    class_: str | None = Field(alias="class")
    #: The observed finding's locus; null when the id names no known finding.
    locus: str | None
    #: The observed finding's summary; null when the id names no known finding.
    summary: str | None


class GoneFindingView(BaseModel):
    finding_id: str
    note: str


class DeliveredSetDeltaView(BaseModel):
    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    added: list[AddedFindingView]
    observed: list[ObservedFindingView]
    gone: list[GoneFindingView]


class RunDeltaView(WireModel):
    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: Annotated[RunMode | Unrecognized, OPEN]
    outcome: Annotated[ChunkStatus | Unrecognized, OPEN]
    escalation: RunEscalationView | None
    sets: list[DeliveredSetDeltaView]
    #: The id of the project this belongs to; ``None`` where the hub reports none.
    project_id: str | None = None
    #: The project's current slug; ``None`` where the hub reports none.
    project_slug: str | None = None
    #: The project's current display name; ``None`` where the hub reports none.
    project_name: str | None = None
