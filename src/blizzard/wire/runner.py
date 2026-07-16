"""Fleet-registry wire bodies (D-019/D-070/D-043).

The registry surface the CLI's fleet verbs, the board, and the runners themselves speak
(design/hub/api.md): a runner registers (``POST /runners``) and heartbeats
(``POST /runners/{id}/heartbeats``); the board lists the fleet with liveness
(``GET /runners``); the operator sets the pause brake (``POST /runners/{id}/pause`` /
``/resume``); and the runner reads its own declarative state back on its pull
(``GET /runners/{id}``). ``online`` and ``paused`` are **derived** (D-004) — liveness
from ``last_seen_at`` against the staleness threshold, paused from the newest pause fact.
"""

from __future__ import annotations

from pydantic import BaseModel


class RunnerRegistrationRequest(BaseModel):
    """Register a runner into the fleet — runner id + workspace binding (D-019)."""

    runner_id: str
    workspace_id: str


class RunnerRegistrationResponse(BaseModel):
    """The registered runner's id, and whether this call first created its row."""

    runner_id: str
    first_registration: bool


class RunnerView(BaseModel):
    """One fleet-registry row — derived liveness and both brakes (D-004/D-070/D-043).

    A runner can be paused by two different parties for two different reasons, so the two
    are reported separately rather than collapsed into one ``paused`` (issue #43): the
    board shows *which*, and a reader that wants "is it claiming?" ORs them.
    """

    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    hub_paused: bool  # the fleet paused it — `blizzard hub pause`, cleared by `hub resume`
    locally_paused: bool = False  # it paused itself — `blizzard runner pause`, cleared by `runner start`


class RunnerListResponse(BaseModel):
    """The fleet registry — every registered runner with its liveness (D-070)."""

    runners: list[RunnerView] = []


class RunnerPauseRequest(BaseModel):
    """Set a runner's pause brake — records who flipped it (D-043)."""

    by: str = "operator"
