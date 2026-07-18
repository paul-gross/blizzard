"""The runner-local held-environments list — ``GET /api/environments`` (issue #51).

``blizzard runner status``'s held-environments section: every environment this
runner currently holds, with the chunk it is bound to and when. Derived at read
time from the same ``held`` binding facts FILL's own capacity math and
``held_environment_ids`` read (``bzh:facts-not-status``) — no separate stored list.

Read-only over its wiring (``bzh:controller-read-only``): the edge holds only the
composition-root-wired :class:`~blizzard.runner.domain.status.RunnerStatusService`.
On the store-free app the service is unwired and the probe answers 503 rather than
pretending.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.domain.status import RunnerStatusService
from blizzard.wire.runner_status import EnvironmentListResponse, HeldEnvironmentView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/environments", response_model=EnvironmentListResponse)
def list_environments(request: Request) -> EnvironmentListResponse:
    """Every environment this runner currently holds, across every chunk."""
    service: RunnerStatusService | None = getattr(request.app.state, "runner_status", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner status service not wired — start via `blizzard runner host`",
        )
    return EnvironmentListResponse(
        items=[
            HeldEnvironmentView(
                environment_id=held.environment_id, chunk_id=held.chunk_id, held_since=iso_utc(held.held_since)
            )
            for held in service.held_environments()
        ]
    )
