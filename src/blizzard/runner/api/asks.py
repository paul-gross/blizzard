"""The runner-local ask endpoint — ``POST /api/leases/{lease_id}/asks`` ([ask-answer.md]).

A worker facing an undecidable choice runs ``blizzard runner ask`` and ends its turn
(ask-and-exit, D-010/D-015). That verb is a pure client of this endpoint (D-023): it
posts the question with the lease id it inherited from the spawn environment
(``BLIZZARD_LEASE_ID``), and the daemon records the ask fact **before** the worker
exits — which is how ADVANCE later tells "parked on a question" from "died without a
verdict" (D-009). The runner mints the ``question_id`` here so it can poll the hub for
the answer by it, and forwards the question up on its next tick.

Read-only over its wiring (``bzh:controller-read-only``): it records through the store
the ``host`` composition root wired on ``app.state``. On the store-free app the store
is unwired and the probe answers 503 rather than pretending.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import QUESTION_PREFIX, mint
from blizzard.runner.store.repository import IWriteRunnerStore

router = APIRouter(prefix="/api", tags=["runner"])


class AskRequest(BaseModel):
    """A worker's ask: the question and its optional pipe-separated choices."""

    question: str
    options: list[str] = []


class AskResponse(BaseModel):
    """The recorded ask — its minted question id (openapi-ts consumes this)."""

    recorded: bool
    question_id: str
    lease_id: str


@router.post("/leases/{lease_id}/asks", response_model=AskResponse, status_code=status.HTTP_201_CREATED)
def record_ask(lease_id: str, request_body: AskRequest, request: Request) -> AskResponse:
    """Record a worker's ask against its lease, minting the question id ([ask-answer.md])."""
    store: IWriteRunnerStore | None = getattr(request.app.state, "runner_store", None)
    clock: IClock | None = getattr(request.app.state, "clock", None)
    if store is None or clock is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    lease = store.active_lease(lease_id)
    if lease is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no active lease {lease_id}")
    question_id = mint(QUESTION_PREFIX, clock)
    store.record_ask(
        lease_id=lease_id,
        chunk_id=lease.chunk_id,
        question_id=question_id,
        question=request_body.question,
        options=request_body.options,
        session_id=lease.session_id,
        asked_at=clock.now(),
    )
    return AskResponse(recorded=True, question_id=question_id, lease_id=lease_id)
