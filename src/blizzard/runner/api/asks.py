"""The runner-local ask endpoints — ``POST /api/leases/{lease_id}/asks`` (record) and
``GET /api/asks?open=true`` (list).

The POST records the ask fact **before** the asking worker exits, which is what lets a later read
tell "parked on a question" from "died without a verdict"; the ``question_id`` is minted here so
the answer can be polled for by it. The GET derives open asks from the same facts, hub-free."""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.foundation.store.utc import iso_utc
from blizzard.runner.api.federation import require_human_api
from blizzard.runner.api.lease_scope import authorized_worker_lease
from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.environments.repository import IReadEnvironmentRepository
from blizzard.runner.leases.asks import AskOnClosedLease, IReadAskRepository, OpenAsk
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

    Token-authorized like every other worker verb, since activeness alone would admit an open takeover's
    closed reference lease too. ``409`` when the lease is that closed reference lease."""
    worker = authorized_worker_lease(lease_id, request)
    try:
        question_id = (
            RunnerWiring.of(request)
            .asks()
            .record_ask(worker, question=request_body.question, options=request_body.options)
        )
    except AskOnClosedLease as exc:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=str(exc)) from exc
    return AskResponse(recorded=True, question_id=question_id, lease_id=lease_id)


def _ask_view(ask: OpenAsk, project_id: str | None) -> AskView:
    return AskView(
        question_id=ask.question_id,
        chunk_id=ask.chunk_id,
        lease_id=ask.lease_id,
        question=ask.question,
        options=ask.options,
        session_id=ask.session_id,
        harness_id=ask.harness_id,
        asked_at=iso_utc(ask.asked_at),
        project_id=project_id,
    )


@router.get("/asks", response_model=AskListResponse, dependencies=[Depends(require_human_api)])
def list_asks(request: Request, open_only: bool = Query(True, alias="open")) -> AskListResponse:
    """Every ask still awaiting an answer — ``GET /api/asks?open=true``.

    The one **human-web-lane** route on this otherwise worker-hook router, so it carries
    ``require_human_api``. An ask reads open while its ``question_id`` carries
    no answer fact. No closed-ask history is kept, so ``open=false`` is refused."""
    if not open_only:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail="only open asks are queryable — no closed-ask history is kept",
        )
    stores = RunnerWiring.of(request).read_stores()
    return _ask_list(stores.asks, stores.environments)


def _ask_list(asks: IReadAskRepository, environments: IReadEnvironmentRepository) -> AskListResponse:
    open_asks = asks.open_asks()
    projects = environments.chunk_project_ids(sorted({a.chunk_id for a in open_asks}))
    return AskListResponse(items=[_ask_view(a, projects.get(a.chunk_id)) for a in open_asks])
