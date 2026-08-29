"""Garden-proposal routes — the read half over garden proposals (blizzard#390).

Nothing writes here yet — passing/accepting a proposal is a sibling issue (D8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.garden_proposals import GardenProposal
from blizzard.wire.garden_proposal import GardenProposalView

router = APIRouter(prefix="/api", tags=["garden-proposals"], dependencies=[Depends(reject_runner_principal)])


def _proposal_view(proposal: GardenProposal) -> GardenProposalView:
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `_finding_view` shape.
    return GardenProposalView.model_validate(
        {
            "proposal_id": proposal.proposal_id,
            "routine_name": proposal.routine_name,
            "class": proposal.class_,
            "title": proposal.title,
            "body": proposal.body,
            "findings": list(proposal.findings),
            "created_at": iso_utc(proposal.created_at),
        }
    )


@router.get("/garden-proposals", response_model=list[GardenProposalView], dependencies=[Depends(require(FLEET_VIEW))])
def list_garden_proposals(services: Annotated[HubServices, Depends(get_services)]) -> list[GardenProposalView]:
    """Every garden proposal, newest first."""
    return [_proposal_view(p) for p in services.garden_proposals.list_all()]


@router.get(
    "/garden-proposals/{proposal_id}",
    response_model=GardenProposalView,
    dependencies=[Depends(require(FLEET_VIEW))],
)
def get_garden_proposal(
    proposal_id: str, services: Annotated[HubServices, Depends(get_services)]
) -> GardenProposalView:
    """One garden proposal's whole record; 404 on an unknown id."""
    proposal = services.garden_proposals.get(proposal_id)
    if proposal is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown garden proposal {proposal_id}")
    return _proposal_view(proposal)
