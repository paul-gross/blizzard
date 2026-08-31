"""Garden-proposal wire shapes (blizzard#390) — the submitted candidate and the read
view. Named `GardenProposal*` throughout — never the bare `Proposal` a work-item
proposal already claims (D1). `closure`/`item_outcome` type on the domain's own enums,
request and response alike (blizzard#395, the `status: ChunkStatus` precedent)."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosureKind, GardenProposalItemOutcome


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


class GardenProposalClosureView(BaseModel):
    """How a garden proposal closed (blizzard#395) — a pass or an accept, either way
    terminal."""

    closure: GardenProposalClosureKind
    reason: str | None
    closed_by: str
    closed_at: str
    item_outcome: GardenProposalItemOutcome | None
    source: str | None
    ref: str | None


class GardenProposalView(BaseModel):
    """A garden proposal, its closure carried alongside it once one exists."""

    model_config = ConfigDict(populate_by_name=True)

    proposal_id: str
    routine_name: str
    class_: str = Field(alias="class")
    title: str
    body: str
    findings: list[str]
    created_at: str
    closure: GardenProposalClosureView | None = None


class GardenProposalPassRequest(BaseModel):
    """`POST /api/garden-proposals/{proposal_id}/pass` — passing wants a reason more
    than accepting does (blizzard#395)."""

    model_config = ConfigDict(extra="forbid")

    reason: str


class GardenProposalAcceptRequest(BaseModel):
    """`POST /api/garden-proposals/{proposal_id}/accept` (blizzard#395). `mint_work_item`
    defaults to `True`: minting a linked hub work item is the default, and declining it
    is the deliberate act. `body` carries the minted item's body when the proposal's own
    body should not be used verbatim; ignored when `mint_work_item` is `False`."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
    body: str | None = None
    mint_work_item: bool = True


class GardenProposalAcceptResponse(GardenProposalView):
    """`POST /api/garden-proposals/{proposal_id}/accept` — the proposal view, its fresh
    closure included, plus the minted item's chunk id (null when acceptance declined to
    mint)."""

    chunk_id: str | None
