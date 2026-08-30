"""The runner-local ask endpoints — ``POST /api/leases/{lease_id}/asks`` (record) and
``GET /api/asks?open=true`` (list, issue #51).

The POST records the ask fact **before** the asking worker exits, which is what lets a later read
tell "parked on a question" from "died without a verdict"; the ``question_id`` is minted here so
the answer can be polled for by it. The GET derives open asks from the same facts, hub-free."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.ids import QUESTION_PREFIX, Id
from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.lease_scope import authorized_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.auth.federation import require_human_api
from blizzard.runner.domain.asks import AskRecord, IReadAskRepository
from blizzard.wire.runner_status import AskListResponse, AskView

router = APIRouter(prefix="/api", tags=["runner"])


class AskRequest(BaseModel):
    """A worker's ask: the question and its optional pipe-separated choices."""

    question: str
    options: list[str] = []


class AskResponse(BaseModel):
    """The recorded ask — its minted question id."""

    recorded: bool
    question_id: str
    lease_id: str


@router.post("/leases/{lease_id}/asks", response_model=AskResponse, status_code=status.HTTP_201_CREATED)
def record_ask(lease_id: str, request_body: AskRequest, request: Request) -> AskResponse:
    """Record a worker's ask against its lease, minting the question id.

    Token-authorized like every other worker verb (issue #291) — previously activeness was
    this route's whole gate, which would have widened admission with no credential behind it
    once an open takeover's closed reference lease qualified too."""
    wiring = RunnerWiring.of(request)
    asks, clock = wiring.stores().asks, wiring.clock()
    lease = authorized_lease(lease_id, request)
    question_id = Id.mint(QUESTION_PREFIX, clock).value
    asks.record_ask(
        lease_id=lease_id,
        chunk_id=lease.chunk_id,
        question_id=question_id,
        question=request_body.question,
        options=request_body.options,
        session_id=lease.session_id,
        asked_at=clock.now(),
    )
    events = wiring.events()
    if events is not None:
        events.publish_ask_changed(lease_id, lease.chunk_id, question_id, cause="asked")
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

    The one **human-web-lane** route on this otherwise worker-hook router, so it carries
    ``require_human_api`` (issue #95). An ask reads open while its ``question_id`` carries
    no answer fact. No closed-ask history is kept, so ``open=false`` is refused."""
    if not open_only:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only open asks are queryable — no closed-ask history is kept",
        )
    return _ask_list(RunnerWiring.of(request).stores().asks)


def _ask_list(asks: IReadAskRepository) -> AskListResponse:
    return AskListResponse(items=[_ask_view(a) for a in asks.open_asks()])
