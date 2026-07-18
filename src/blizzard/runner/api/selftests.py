"""The selftest job resource — ``POST /api/selftests``, ``GET /api/selftests/{id}``
(issue #54).

The adapter-drift canary as a resource with a result, not an RPC verb: POST mints a
run against a chosen coding harness and returns immediately (the checks run off the
request thread, ``runner/selftest/service.py``); GET re-reads it. Confined to the
in-memory :class:`~blizzard.runner.selftest.service.SelfTestService` the composition
root wires unconditionally on ``app.state`` — no store is needed on this path, so
even the store-free app (OpenAPI export, unit tests) answers both routes, its empty
harness registry making ``POST`` answer 422 naming no configured harnesses, exactly
like a real misconfiguration would.
"""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.runner.selftest.model import SelfTestRun
from blizzard.runner.selftest.service import SelfTestService, UnknownHarnessError

router = APIRouter(prefix="/api", tags=["runner"])


class SelfTestStartRequest(BaseModel):
    """Which coding harness to run the canary against."""

    harness: str


class SelfTestCheckView(BaseModel):
    """One check's pass/fail result (openapi-ts consumes this)."""

    name: str
    passed: bool
    detail: str


class SelfTestView(BaseModel):
    """A selftest run's current state — ``running`` until every check has resolved."""

    id: str
    harness: str
    status: str
    checks: list[SelfTestCheckView]
    error: str | None = None


def _view(run: SelfTestRun) -> SelfTestView:
    return SelfTestView(
        id=run.id,
        harness=run.harness,
        status=run.status,
        checks=[SelfTestCheckView(name=c.name, passed=c.passed, detail=c.detail) for c in run.checks],
        error=run.error,
    )


def _service(request: Request) -> SelfTestService:
    service: SelfTestService | None = getattr(request.app.state, "selftests", None)
    if service is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail="selftest service not wired — start via `blizzard runner host`",
        )
    return service


@router.post("/selftests", response_model=SelfTestView, status_code=status.HTTP_201_CREATED)
def start_selftest(request_body: SelfTestStartRequest, request: Request) -> SelfTestView:
    """Mint a selftest run against ``harness`` and begin it off the request thread."""
    service = _service(request)
    try:
        run = service.start(request_body.harness)
    except UnknownHarnessError as exc:
        known = ", ".join(exc.known) or "(none configured)"
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=f"unknown coding harness {exc.harness!r} — configured harnesses: {known}",
        ) from exc
    return _view(run)


@router.get("/selftests/{selftest_id}", response_model=SelfTestView)
def get_selftest(selftest_id: str, request: Request) -> SelfTestView:
    """Read back a selftest run's current state."""
    service = _service(request)
    run = service.get(selftest_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no selftest run {selftest_id}")
    return _view(run)
