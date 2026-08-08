"""Fleet-registry wire bodies.

``online`` and ``paused`` are **derived** — liveness from ``last_seen_at`` against the
staleness threshold, paused from the newest pause fact.
:class:`RunnerEnrollmentResponse` (issue #86a) is the one body that ever carries a
runner's plaintext bearer token."""

from __future__ import annotations

from pydantic import BaseModel


class RunnerRegistrationRequest(BaseModel):
    """Register a runner into the fleet — runner id + workspace binding.

    ``env_capacity`` is the runner's configured environment-pool size; ``None`` when the
    client reports none, never a guessed total. Re-registration overwrites it."""

    runner_id: str
    workspace_id: str
    env_capacity: int | None = None
    #: The runner's own browser-reachable base URL (issue #95) — optional; a runner that
    #: registers none cannot be an IdP-authorize ``client``.
    url: str | None = None
    #: The allowed redirect URIs a browser may be bounced to for this runner (issue #95)
    #: — exact-match only (the open-redirect guard). Empty registers none.
    redirect_uris: list[str] = []


class RunnerRegistrationResponse(BaseModel):
    """The registered runner's id, and whether this call first created its row."""

    runner_id: str
    first_registration: bool


class RunnerEnrollmentResponse(BaseModel):
    """A freshly minted (or rotated) bearer token — issue #86a.

    ``token`` is the plaintext, visible only here — only its sha256 hash is kept. A
    re-enroll rotates: the old token stops resolving the moment this response lands."""

    runner_id: str
    token: str


class ExternalSubscriptionUsageWindowView(BaseModel):
    """One rate-limit window's utilization, as the harness's own account reported it
    (issue #218) — ``window`` is the harness-native label, ``utilization_pct`` is 0-100,
    ``resets_at`` the reset instant, ``window_seconds`` the window's length."""

    window: str
    utilization_pct: float
    resets_at: str
    window_seconds: int


class ExternalSubscriptionUsageView(BaseModel):
    """A runner's newest sampled external-subscription-usage snapshot (issue #218)."""

    sampled_at: str
    windows: list[ExternalSubscriptionUsageWindowView]


class RunnerView(BaseModel):
    """One fleet-registry row — derived liveness, both brakes, and an advisory snapshot.

    The two brakes stay separate (issues #43, #45): ``hub_paused`` is claims-only, while
    ``locally_paused`` answers "is it spawning at all?". The usage snapshot is advisory."""

    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    hub_paused: bool  # the fleet paused it — `blizzard hub runner pause`, cleared by `hub runner resume`
    locally_paused: bool = False  # it paused itself — spawns nothing, `blizzard runner pause`/`start`
    # The local pause's own cause, populated only alongside a true `locally_paused`
    # (issue #61); `reason` is `None` for a manual pause.
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    # The configured environment-pool size — ``None`` when none was reported, never zero.
    env_capacity: int | None = None
    # The newest external-subscription-usage sample (issue #218) — absent when none was
    # ever sampled, or the newest is stale; never a fabricated empty value.
    external_subscription_usage: ExternalSubscriptionUsageView | None = None


class RunnerListResponse(BaseModel):
    """The fleet registry — every registered runner with its liveness."""

    runners: list[RunnerView] = []


class RunnerPauseRequest(BaseModel):
    """Set a runner's pause brake — records who flipped it."""

    by: str = "operator"
