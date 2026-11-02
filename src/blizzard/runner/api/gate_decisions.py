"""``GET /api/gate-decisions`` — the runner's own open gate decisions, forwarded from the hub.

A resolving transition leaves no local fact, so the runner cannot tell a parked gate from a resolved one on its own;
the hub holds the decisions and this forwards its answer for the runner's identity. A hub that cannot be reached is
this route's failure, as it is for every forward (``bzh:hub-proxy``)."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request

from blizzard.runner.api.federation import require_human_api
from blizzard.runner.api.hub_proxy import HubProxy
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.wire.decision import OpenDecisionsResponse

router = APIRouter(prefix="/api", tags=["runner"])

#: The whole-forward budget, so a down hub fails ``status`` quickly rather than after the full retry ceiling.
_FORWARD_TIMEOUT_SECONDS = 3.0


@router.get("/gate-decisions", response_model=OpenDecisionsResponse, dependencies=[Depends(require_human_api)])
def list_gate_decisions(request: Request) -> OpenDecisionsResponse:
    """The unresolved decisions this runner's configuration imposed, as the hub holds them. A runner that has not
    registered has imposed none."""
    identity = RunnerWiring.of(request).identity().current()
    if identity is None:
        return OpenDecisionsResponse()
    upstream = HubProxy.of(request, "gate-decisions").get(
        f"/api/fleet/runners/{identity.runner_id}/decisions", timeout=_FORWARD_TIMEOUT_SECONDS
    )
    return OpenDecisionsResponse.model_validate(upstream.json())
