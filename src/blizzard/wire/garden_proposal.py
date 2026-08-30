"""Garden-proposal wire shapes (blizzard#390) — the submitted candidate and the read
view. Named `GardenProposal*` throughout — never the bare `Proposal` a work-item
proposal already claims (D1)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GardenProposalCandidate(BaseModel):
    """A run's proposed response to one or more findings — no id, minted at delivery.
    `ref` is stable only within its own submission. `findings` is required and
    non-empty (D7): a proposal with nothing behind it is an opinion the run was not
    asked for."""

    model_config = ConfigDict(populate_by_name=True)

    ref: str
    class_: str = Field(alias="class")
    title: str
    body: str
    findings: list[str] = Field(min_length=1)


class GardenProposalView(BaseModel):
    """A garden proposal."""

    model_config = ConfigDict(populate_by_name=True)

    proposal_id: str
    routine_name: str
    class_: str = Field(alias="class")
    title: str
    body: str
    findings: list[str]
    created_at: str
