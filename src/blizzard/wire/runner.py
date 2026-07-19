"""Fleet-registry wire bodies.

The registry surface the CLI's fleet verbs, the board, and the runners themselves speak
: a runner registers (``POST /runners``) and heartbeats
(``POST /runners/{id}/heartbeats``); the board lists the fleet with liveness
(``GET /runners``); the operator sets the pause brake (``POST /runners/{id}/pause`` /
``/resume``); and the runner reads its own declarative state back on its pull
(``GET /runners/{id}``). ``online`` and ``paused`` are **derived** — liveness
from ``last_seen_at`` against the staleness threshold, paused from the newest pause fact.

``POST /runners/{id}/enrollments`` (issue #86a) mints or rotates the runner's bearer
token, returning :class:`RunnerEnrollmentResponse` — the one response that ever carries
the plaintext.
"""

from __future__ import annotations

from pydantic import BaseModel


class RunnerRegistrationRequest(BaseModel):
    """Register a runner into the fleet — runner id + workspace binding.

    ``env_capacity`` is the runner's configured environment-pool size (the length of its
    ``workspace_envs``) — the denominator the board's slot bar renders ``used/total``
    against. Absent (``None``) from an older runner binary that predates this field, in
    which case the hub stores/reports null and the board omits the bar rather than
    guessing a total. Re-registration is the runner's heartbeat, so a ``workspace_envs``
    change converges on the next pull (the stored value is overwritten unconditionally)."""

    runner_id: str
    workspace_id: str
    env_capacity: int | None = None


class RunnerRegistrationResponse(BaseModel):
    """The registered runner's id, and whether this call first created its row."""

    runner_id: str
    first_registration: bool


class RunnerEnrollmentResponse(BaseModel):
    """A freshly minted (or rotated) bearer token — issue #86a.

    ``token`` is the plaintext; the hub keeps only its sha256 hash from here on, so
    this response is the one and only place it is ever visible again. A re-enroll
    call rotates: the old token stops resolving the moment this response lands."""

    runner_id: str
    token: str


class RunnerView(BaseModel):
    """One fleet-registry row — derived liveness and both brakes.

    A runner can be paused by two different parties for two different reasons, so the two
    are reported separately rather than collapsed into one ``paused`` (issue #43): the
    board shows *which*. A reader that wants "is it claiming?" ORs them; since issue #45
    the two diverge past claiming — ``hub_paused`` keeps its claims-only meaning, while
    ``locally_paused`` alone answers "is it spawning anything at all?".
    """

    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    hub_paused: bool  # the fleet paused it — `blizzard hub pause`, cleared by `hub resume`
    locally_paused: bool = False  # it paused itself — spawns nothing, `blizzard runner pause`/`start`
    # The local pause's own cause, populated only alongside a true `locally_paused` (issue
    # #61): `by` is "operator" for `blizzard runner pause`, "runner-ceiling" for a spend-
    # ceiling crossing; `reason` is the composed ceiling+spend string, `None` for a manual
    # pause.
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    # The runner's configured environment-pool size — the ``total`` denominator the board's
    # slot bar renders ``used/total`` against. ``None`` for a runner registered by a client
    # that predates this field; the board omits the bar (not a zero-slot bar) when null.
    env_capacity: int | None = None


class RunnerListResponse(BaseModel):
    """The fleet registry — every registered runner with its liveness."""

    runners: list[RunnerView] = []


class RunnerPauseRequest(BaseModel):
    """Set a runner's pause brake — records who flipped it."""

    by: str = "operator"
