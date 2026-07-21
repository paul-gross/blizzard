"""The runner-local ask endpoints — ``POST /api/leases/{lease_id}/asks`` (record) and
``GET /api/asks?open=true`` (list, issue #51).

A worker facing an undecidable choice runs ``blizzard runner ask`` and ends its turn
(ask-and-exit). That verb is a pure client of the POST endpoint: it
posts the question with the lease id it inherited from the spawn environment
(``BLIZZARD_LEASE_ID``), and the daemon records the ask fact **before** the worker
exits — which is how ADVANCE later tells "parked on a question" from "died without a
verdict". The runner mints the ``question_id`` here so it can poll the hub for
the answer by it, and forwards the question up on its next tick. The GET endpoint is
``blizzard runner status``'s open-asks section — every ask not yet answered, derived
from the same facts, hub-free.

Read-only over its wiring (``bzh:controller-read-only``): it records through the store
the ``host`` composition root wired on ``app.state``. On the store-free app the store
is unwired and the probe answers 503 rather than pretending.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.clock import IClock
from blizzard.foundation.ids import QUESTION_PREFIX, mint
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.auth.federation import require_human_api
from blizzard.runner.store.repository import AskRecord, IReadRunnerStore, IWriteRunnerStore
from blizzard.wire.runner_status import AskListResponse, AskView

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
    """Record a worker's ask against its lease, minting the question id."""
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


def _ask_view(ask: AskRecord) -> AskView:
    return AskView(
        question_id=ask.question_id,
        chunk_id=ask.chunk_id,
        lease_id=ask.lease_id,
        question=ask.question,
        options=ask.options,
        session_id=ask.session_id,
        asked_at=iso_utc(ask.asked_at),
    )


@router.get("/asks", response_model=AskListResponse, dependencies=[Depends(require_human_api)])
def list_asks(request: Request, open_only: bool = Query(True, alias="open")) -> AskListResponse:
    """Every ask still awaiting an answer — ``GET /api/asks?open=true`` (issue #51).

    The one **human-web-lane** route on this otherwise worker-hook router: it is the
    panel's / ``blizzard runner status``'s open-asks read, so it carries
    ``require_human_api`` (issue #95) while the worker-hook POST above stays ungated. Over
    the socket and under a ``none``-mode hub the gate resolves to the implicit identity.

    Derived, hub-free: an ask reads open while its ``question_id`` carries no answer
    fact (:meth:`~blizzard.runner.store.repository.IReadRunnerStore.open_asks`), whether
    or not it has been forwarded to the hub yet. There is no closed-ask history to serve,
    so ``open=false`` is refused rather than silently answered as if it were true.
    """
    if not open_only:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only open asks are queryable — no closed-ask history is kept",
        )
    store: IReadRunnerStore | None = getattr(request.app.state, "runner_store", None)
    if store is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="runner store not wired — start via `blizzard runner host`",
        )
    return AskListResponse(items=[_ask_view(a) for a in store.open_asks()])
