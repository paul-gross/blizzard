"""Sweeps on request: one pass of a hub sweep, answered once it has finished. ``/api/sweeps`` runs a pass over the
request's tenant alone, under :attr:`~blizzard.auth_core.Permission.ANALYTICS_ADMIN`; ``/api/admin/sweeps`` runs one
over every tenant, exactly as a timed pass runs, under the hub-level
:attr:`~blizzard.auth_core.Permission.TENANT_ADMIN`. Both serve a sweep in either mode.
Each handler is sync, so the pass runs off the event loop and every write it makes lands before the answer."""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, HTTPException, Request, status
from fastapi.responses import JSONResponse

from blizzard.auth_core import Permission
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import TenantRequest, require, require_hub, resolve_tenant, tenant_bound
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.sweeps.locks import SweepInProgress
from blizzard.hub.domain.sweeps.roster import SweepPassResult, SweepServesNoTenant, UnknownSweep
from blizzard.wire.sweep_passes import SweepPassResponse

router = APIRouter(
    prefix="/api/sweeps", tags=["sweeps"], dependencies=[Depends(reject_runner_principal), Depends(tenant_bound)]
)
admin_router = APIRouter(prefix="/api/admin/sweeps", tags=["admin"], dependencies=[Depends(reject_runner_principal)])

_UNKNOWN: dict[int | str, dict[str, Any]] = {
    status.HTTP_404_NOT_FOUND: {"description": "No sweep this hub builds goes by the name."}
}
_IN_PROGRESS: dict[int | str, dict[str, Any]] = {
    status.HTTP_409_CONFLICT: {
        "description": 'A pass of the sweep holds the lock this one needs: `{"code": "sweep_in_progress"}`.'
    }
}


def _response(result: SweepPassResult) -> SweepPassResponse:
    return SweepPassResponse(
        started_at=iso_utc(result.started_at),
        finished_at=iso_utc(result.finished_at),
        outcome=result.outcome,
        error=result.error,
    )


def _in_progress() -> JSONResponse:
    return JSONResponse(status_code=status.HTTP_409_CONFLICT, content={"code": "sweep_in_progress"})


@router.post(
    "/{name}/passes",
    response_model=SweepPassResponse,
    dependencies=[Depends(require(Permission.ANALYTICS_ADMIN))],
    responses={
        **_UNKNOWN,
        status.HTTP_422_UNPROCESSABLE_CONTENT: {"description": "The sweep runs over every tenant only."},
        **_IN_PROGRESS,
    },
)
def run_tenant_pass(
    name: str, tenant: Annotated[TenantRequest, Depends(resolve_tenant)], hub: Hub
) -> SweepPassResponse | JSONResponse:
    """One pass of ``forge_status``, ``event_derivation`` or ``close_drain`` over the request's tenant alone:
    ``failed`` with the fault its tenant met."""
    try:
        return _response(hub.sweeps.pass_one_tenant(name, tenant.scope.tenant))
    except UnknownSweep as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SweepServesNoTenant as exc:
        raise HTTPException(status_code=status.HTTP_422_UNPROCESSABLE_CONTENT, detail=str(exc)) from exc
    except SweepInProgress:
        return _in_progress()


@admin_router.post(
    "/{name}/passes",
    response_model=SweepPassResponse,
    dependencies=[Depends(require_hub(Permission.TENANT_ADMIN))],
    responses={**_UNKNOWN, **_IN_PROGRESS},
)
def run_every_tenant_pass(name: str, request: Request, hub: Hub) -> SweepPassResponse | JSONResponse:
    """One pass of any sweep this hub builds over every tenant, under its ``sweep <name>`` root: ``failed`` only
    when the pass itself raised."""
    try:
        return _response(hub.sweeps.pass_every_tenant(name, request.app.state.platform_tracing.tracer))
    except UnknownSweep as exc:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=str(exc)) from exc
    except SweepInProgress:
        return _in_progress()
