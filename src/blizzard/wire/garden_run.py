"""A garden routine run's own read views — the run list and one run's own delta."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.chunk_status import ChunkStatus


class DeliveredSetView(BaseModel):
    finding_set_id: str
    revisions: dict[str, str]
    measurement: str | None
    #: How many `add`/`observed`/`gone` facts this delivery recorded — this delivery's
    #: own act, never a finding's whole life history. `0`, never null, when a kind is
    #: absent. Named with the `_count` suffix, distinct from `DeliveredSetDeltaView`'s
    #: own full `added`/`observed`/`gone` lists.
    added_count: int
    observed_count: int
    gone_count: int


class RunEscalationView(BaseModel):
    node_name: str | None
    takeover_command: str
    wrapped_takeover_command: str


class RunRowView(BaseModel):
    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    minted_at: str
    outcome: ChunkStatus
    escalation: RunEscalationView | None
    delivered: list[DeliveredSetView]


class AddedFindingView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    finding_id: str | None = Field(
        description="The finding this add minted, or null when the delivered set predates "
        "the finding_facts.finding_set_id linkage — the add still renders from the "
        "artifact, just linked to no finding row."
    )
    class_: str = Field(alias="class")
    locus: str
    summary: str
    introduced: str | None


class ObservedFindingView(BaseModel):
    model_config = ConfigDict(populate_by_name=True)

    finding_id: str
    #: The finding row's own class/locus/summary — an observed op names only an id, so
    #: these are read back from the finding it points at, and are null when it names no
    #: finding row: the entry still renders by id rather than being dropped.
    class_: str | None = Field(alias="class")
    locus: str | None
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


class RunDeltaView(BaseModel):
    chunk_id: str
    routine_name: str
    scope_slug: str
    mode: str
    outcome: ChunkStatus
    escalation: RunEscalationView | None
    sets: list[DeliveredSetDeltaView]
