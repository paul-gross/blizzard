"""Finding routes — the read half over findings (blizzard#390).

``GET /api/findings`` is the pass's own bucket read
(blizzard-product:/plans/garden/machinery.md §Managing findings and proposals): a
routine's live findings under one scope. Nothing writes here yet — delivery is a sibling
issue (D8)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Query, status

from blizzard.auth_core import FLEET_VIEW
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require
from blizzard.hub.api.deps import get_services
from blizzard.hub.composition import HubServices
from blizzard.hub.domain.findings import Finding
from blizzard.wire.finding import FindingView

router = APIRouter(prefix="/api", tags=["findings"], dependencies=[Depends(reject_runner_principal)])


def _finding_view(finding: Finding) -> FindingView:
    # `class_`'s alias is the Python keyword `class` — constructed by alias via
    # `model_validate`, the `_proposal_view` shape.
    return FindingView.model_validate(
        {
            "finding_id": finding.finding_id,
            "routine_name": finding.routine_name,
            "scope_slug": finding.scope_slug,
            "class": finding.class_,
            "locus": finding.locus,
            "summary": finding.summary,
            "introduced": finding.introduced,
            "live": finding.live,
            "last_seen_at": iso_utc(finding.last_seen_at) if finding.last_seen_at is not None else None,
            "observed_count": finding.observed_count,
        }
    )


@router.get("/findings", response_model=list[FindingView], dependencies=[Depends(require(FLEET_VIEW))])
def list_findings(
    services: Annotated[HubServices, Depends(get_services)],
    routine: Annotated[str, Query()],
    scope: Annotated[str, Query()],
    include_gone: Annotated[bool, Query()] = False,
) -> list[FindingView]:
    """A routine's findings under one scope — live only, unless `include_gone` (D3);
    the read a running pass calls to cross-reference its own bucket."""
    return [_finding_view(f) for f in services.findings.list_for(routine, scope, include_gone=include_gone)]


@router.get("/findings/{finding_id}", response_model=FindingView, dependencies=[Depends(require(FLEET_VIEW))])
def get_finding(finding_id: str, services: Annotated[HubServices, Depends(get_services)]) -> FindingView:
    """One finding's whole record; 404 on an unknown id."""
    finding = services.findings.get(finding_id)
    if finding is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"unknown finding {finding_id}")
    return _finding_view(finding)
