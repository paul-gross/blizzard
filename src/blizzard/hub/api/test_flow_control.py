"""Test-fenced controls for the hosted flow driver and its shared clock."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends
from pydantic import BaseModel, Field

from blizzard.foundation.clock import FixedClock
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.delivery.flow_driver import FlowDriver


class AdvanceClock(BaseModel):
    seconds: float = Field(ge=0, le=31536000, allow_inf_nan=False)


def flow_control_router(driver: FlowDriver, clock: FixedClock) -> APIRouter:
    """Create controls for one hosted process; the lock covers turns as well as ticks and clock changes."""
    lock = asyncio.Lock()
    router = APIRouter(
        prefix="/api/admin/test-flow",
        dependencies=[Depends(reject_runner_principal), Depends(require_hub(Permission.TENANT_ADMIN))],
        include_in_schema=False,
    )

    @router.post("/step")
    async def step() -> dict[str, object]:
        async with lock:
            task = asyncio.create_task(driver.step())
            try:
                result = await asyncio.shield(task)
            except asyncio.CancelledError:
                await task
                raise
            return {
                "now": clock.now().isoformat(),
                "reconciled": len(result.reconciled),
                "skipped": len(result.skipped),
                "failed": len(result.failed),
                "submitted": result.submitted,
                "closed": result.closed,
                "interrupted": result.interrupted,
                "started": len(result.started),
                "noticed": result.noticed,
            }

    @router.post("/advance")
    async def advance(body: AdvanceClock) -> dict[str, str]:
        async with lock:
            clock.advance(timedelta(seconds=body.seconds))
            return {"now": clock.now().isoformat()}

    return router
