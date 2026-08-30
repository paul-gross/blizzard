"""The selftest job resource — ``POST``/``GET /api/selftests`` (issue #54).

The adapter-drift canary as a resource with a result, not an RPC verb: POST mints a run
and returns immediately, with the checks off the request thread; GET re-reads it. No
store is needed on this path, so an empty harness registry answers 422 naming no
configured harnesses, exactly as a real misconfiguration would."""

from __future__ import annotations

from fastapi import APIRouter, Request, status
from fastapi.exceptions import HTTPException
from pydantic import BaseModel

from blizzard.runner.api.wiring import RunnerWiring
from blizzard.runner.selftest.model import SelfTestRun
from blizzard.runner.selftest.service import UnknownHarnessError

router = APIRouter(prefix="/api", tags=["runner"])


class SelfTestStartRequest(BaseModel):
    """Which coding harness to run the canary against."""

    harness: str


class SelfTestCheckView(BaseModel):
    """One check's pass/fail result."""

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


@router.post("/selftests", response_model=SelfTestView, status_code=status.HTTP_201_CREATED)
def start_selftest(request_body: SelfTestStartRequest, request: Request) -> SelfTestView:
    """Mint a selftest run against ``harness`` and begin it off the request thread."""
    service = RunnerWiring.of(request).selftests()
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
    service = RunnerWiring.of(request).selftests()
    run = service.get(selftest_id)
    if run is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail=f"no selftest run {selftest_id}")
    return _view(run)
