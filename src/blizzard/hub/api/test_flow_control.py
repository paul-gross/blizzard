"""Test-fenced controls for the hosted flow driver and its shared clock."""

from __future__ import annotations

import asyncio
from datetime import timedelta

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from blizzard.foundation.clock import ControlledClock, FixedClock, IClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.store.utc import iso_utc
from blizzard.hub.api.auth import reject_runner_principal
from blizzard.hub.api.auth_session import require_hub
from blizzard.hub.auth.permissions import Permission
from blizzard.hub.delivery.flow_driver import FlowDriver, TickResult


def step_answer(result: TickResult, clock: IClock) -> dict[str, object]:
    return {
        "now": iso_utc(clock.now()),
        "reconciled": len(result.reconciled),
        "skipped": len(result.skipped),
        "failed": len(result.failed),
        "submitted": result.submitted,
        "closed": result.closed,
        "interrupted": result.interrupted,
        "started": len(result.started),
        "noticed": result.noticed,
    }


def controlled_flow_router(driver: FlowDriver, clock: ControlledClock, lock: asyncio.Lock) -> APIRouter:
    router = APIRouter(
        prefix="/api/admin/test-flow",
        dependencies=[Depends(reject_runner_principal), Depends(require_hub(Permission.TENANT_ADMIN))],
        include_in_schema=False,
    )

    @router.post("/step")
    async def step() -> dict[str, object]:
        async with lock:
            task = asyncio.create_task(driver.step())
            cancelled = False
            while True:
                try:
                    result = await asyncio.shield(task)
                    break
                except asyncio.CancelledError:
                    if task.cancelled():
                        raise
                    cancelled = True
                except Exception as exc:
                    get_logger("blizzard.hub.flow_driver").exception("flow driver tick failed")
                    if cancelled:
                        raise asyncio.CancelledError from exc
                    raise HTTPException(status_code=500, detail="flow driver step failed") from exc
            if cancelled:
                raise asyncio.CancelledError
            return step_answer(result, clock)

    return router


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
            return step_answer(result, clock)

    @router.post("/advance")
    async def advance(body: AdvanceClock) -> dict[str, str]:
        async with lock:
            clock.advance(timedelta(seconds=body.seconds))
            return {"now": iso_utc(clock.now())}

    return router
