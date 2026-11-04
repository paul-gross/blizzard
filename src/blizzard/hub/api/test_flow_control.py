"""Test-fenced controls for the hosted flow driver and its shared clock."""

from __future__ import annotations

import asyncio

from fastapi import APIRouter, Depends, HTTPException

from blizzard.foundation.clock import IClock
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


def flow_control_router(driver: FlowDriver, clock: IClock, lock: asyncio.Lock) -> APIRouter:
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
