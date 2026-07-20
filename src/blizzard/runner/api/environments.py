"""The runner-local environment-pool list — ``GET /api/environments`` (issue #51,
extended to the full pool by issue #106).

``blizzard runner status``'s environments section: every environment in the
runner's configured pool, held or free — held rows carry the chunk they are
bound to and when; unused rows carry neither. Derived at read time from the
same ``held`` binding facts FILL's own capacity math and
``held_environment_ids`` read (``bzh:facts-not-status``) — no separate stored
list, and no pool fact invented on the wire that the service didn't derive.

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
from blizzard.wire.runner_status import EnvironmentListResponse, EnvironmentView

router = APIRouter(prefix="/api", tags=["runner"])


@router.get("/environments", response_model=EnvironmentListResponse)
def list_environments(request: Request) -> EnvironmentListResponse:
    """Every environment in this runner's configured pool, held or free."""
    service: RunnerStatusService | None = getattr(request.app.state, "runner_status", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner status service not wired — start via `blizzard runner host`",
        )
    return EnvironmentListResponse(
        items=[
            EnvironmentView(
                environment_id=slot.environment_id,
                chunk_id=slot.chunk_id,
                held_since=iso_utc(slot.held_since) if slot.held_since is not None else None,
            )
            for slot in service.environments()
        ]
    )
