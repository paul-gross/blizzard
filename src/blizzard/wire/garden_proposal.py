"""Garden-proposal wire shapes (blizzard#390) — the submitted candidate and the read
view. Named `GardenProposal*` throughout — never the bare `Proposal` a work-item
proposal already claims (D1). `closure`/`item_outcome` type on the domain's own enums,
request and response alike (blizzard#395) — see ``blizzard.wire.work_source``'s module
docstring for the convention."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field

from blizzard.hub.domain.garden_proposal_closure import GardenProposalClosureKind, GardenProposalItemOutcome


class GardenProposalCandidate(BaseModel):
    """A run's proposed response — no id, minted at delivery. `ref` is stable only
    within its own submission. Whether a submission must name any `findings` at all
    is the submitting graph's own decision, never this wire shape's (see
    [blizzard-context/domain/findings-and-proposals.md](https://github.com/paul-gross/blizzard-context/blob/master/domain/findings-and-proposals.md))."""

    model_config = ConfigDict(populate_by_name=True)

    ref: str
    class_: str = Field(alias="class")
    title: str
    body: str
    findings: list[str] = Field(default_factory=list)


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


class GardenProposalsPageView(BaseModel):
    """``GET /api/garden-proposals``'s own bounded page (blizzard#526 D3/D4) —
    ``next_cursor`` is ``None`` exactly when this page is the last one."""

    proposals: list[GardenProposalView] = []
    next_cursor: str | None = None


class GardenProposalPassRequest(BaseModel):
    """`POST /api/garden-proposals/{proposal_id}/pass` — passing wants a reason more
    than accepting does (blizzard#395)."""

    model_config = ConfigDict(extra="forbid")

    reason: str


class GardenProposalAcceptRequest(BaseModel):
    """`POST /api/garden-proposals/{proposal_id}/accept` (blizzard#395). `mint_work_item`
    defaults to `True`: minting a linked hub work item is the default, and declining it
    is the deliberate act. `body` replaces the proposal's own body as the prose the
    minted item's "Related findings" template wraps, when the proposal's own body should
    not be used; ignored when `mint_work_item` is `False` (blizzard#397)."""

    model_config = ConfigDict(extra="forbid")

    reason: str | None = None
    body: str | None = None
    mint_work_item: bool = True


class GardenProposalAcceptResponse(GardenProposalView):
    """`POST /api/garden-proposals/{proposal_id}/accept` — the proposal view, its fresh
    closure included, plus the minted item's chunk id (null when acceptance declined to
    mint)."""

    chunk_id: str | None
