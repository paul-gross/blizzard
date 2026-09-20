"""Fleet-registry wire bodies.

``online`` and ``paused`` are **derived** — liveness from ``last_seen_at`` against the
staleness threshold, paused from the newest pause fact.
:class:`RunnerEnrollmentResponse` (issue #86a) is the one body that ever carries a
runner's plaintext bearer token."""

from __future__ import annotations

from pydantic import BaseModel


class RunnerCapability(BaseModel):
    """One harness binding this runner can execute (blizzard#433) — the id, its observed
    version (``None`` when the binding exposes none), the tier ids it can resolve, and
    whether it is this runner's default binding. ``available`` (blizzard#438) defaults
    ``True`` so a runner asserting none matches exactly as it did before this field
    existed — never a reason to strand a pre-upgrade runner."""

    harness_id: str
    version: str | None = None
    tiers: list[str] = []
    default: bool = False
    available: bool = True


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
    #: The runner's capability snapshot — every harness/tier it can execute right now.
    capabilities: list[RunnerCapability] = []


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


class SubscriptionUsageView(BaseModel):
    """One reported subscription's newest sampled usage, carrying its identity
    (issue #218). ``slug`` is the runner-unique join key, ``name`` the operator-facing
    label."""

    slug: str
    name: str
    sampled_at: str
    windows: list[ExternalSubscriptionUsageWindowView]


class RunnerView(BaseModel):
    """One fleet-registry row — derived liveness, both brakes, and advisory subscription usage.

    The two brakes stay separate (issues #43, #45): ``hub_paused`` is claims-only, while
    ``locally_paused`` answers "is it spawning at all?". Subscription usage is advisory."""

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
    # Every reported per-slug sample's own non-stale usage, empty when none was reported.
    subscriptions: list[SubscriptionUsageView] = []
    # The runner's reported capability snapshot — every harness/tier it can execute right now.
    capabilities: list[RunnerCapability] = []


class RunnerListResponse(BaseModel):
    """The fleet registry — every registered runner with its liveness."""

    runners: list[RunnerView] = []


class RunnerPauseRequest(BaseModel):
    """Set a runner's pause brake — records who flipped it."""

    by: str = "operator"
