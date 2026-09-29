"""Fleet-registry wire bodies.

``online`` and ``paused`` are **derived** — liveness from ``last_seen_at`` against the
staleness threshold, paused from the newest pause fact.
:class:`RunnerEnrollmentResponse` is the one body that ever carries a
runner's plaintext bearer token."""

from __future__ import annotations

from pydantic import BaseModel


class RunnerCapability(BaseModel):
    """One harness binding this runner can execute — the id, its observed
    version (``None`` when the binding exposes none), the tier ids it can resolve, and
    whether it is this runner's default binding. ``available`` defaults
    ``True`` so a runner asserting none matches exactly as it did before this field
    existed — never a reason to strand a pre-upgrade runner."""

    harness_id: str
    version: str | None = None
    tiers: list[str] = []
    default: bool = False
    available: bool = True


class RunnerSubscriptionDeclaration(BaseModel):
    """One provider subscription the runner declares at registration — the join key
    everything else keys off. ``provider`` is stored but reaches no view; nothing reads
    it there yet."""

    slug: str
    name: str
    provider: str


class RunnerRegistrationRequest(BaseModel):
    """Register a runner into the fleet — runner id + workspace binding.

    ``env_capacity`` is the runner's configured environment-pool size; ``None`` when the
    client reports none, never a guessed total. Re-registration overwrites it."""

    runner_id: str
    workspace_id: str
    env_capacity: int | None = None
    #: The runner's own browser-reachable base URL — optional; a runner that
    #: registers none cannot be an IdP-authorize ``client``.
    url: str | None = None
    #: The allowed redirect URIs a browser may be bounced to for this runner
    #: — exact-match only (the open-redirect guard). Empty registers none.
    redirect_uris: list[str] = []
    #: The runner's capability snapshot — every harness/tier it can execute right now.
    capabilities: list[RunnerCapability] = []
    #: The declared subscription roster — ``None`` for no roster, ``[]`` for none declared.
    subscriptions: list[RunnerSubscriptionDeclaration] | None = None


class RunnerRegistrationResponse(BaseModel):
    """The registered runner's id, and whether this call first created its row."""

    runner_id: str
    first_registration: bool


class RunnerEnrollmentResponse(BaseModel):
    """A freshly minted (or rotated) bearer token.

    ``token`` is the plaintext, visible only here — only its sha256 hash is kept. A
    re-enroll rotates: the old token stops resolving the moment this response lands."""

    runner_id: str
    token: str


class ExternalSubscriptionUsageWindowView(BaseModel):
    """One rate-limit window's utilization, as the harness's own account reported it
    — ``window`` is the harness-native label, ``utilization_pct`` is 0-100,
    ``resets_at`` the reset instant, ``window_seconds`` the window's length."""

    window: str
    utilization_pct: float
    resets_at: str
    window_seconds: int


class SubscriptionUsageView(BaseModel):
    """One reported subscription's newest sampled usage, carrying its identity.
    ``slug`` is the runner-unique join key, ``name`` the operator-facing
    label. ``sampled_at`` is ``None`` for a miss-only row — ``condition``
    carries the reason in that case, and ``windows`` is empty."""

    slug: str
    name: str
    sampled_at: str | None = None
    windows: list[ExternalSubscriptionUsageWindowView]
    #: ``"credential_lapsed"`` when the newest reported miss outranks the newest sample; ``None`` otherwise.
    condition: str | None = None
    #: The newest reported miss's own reason; ``None`` when there is none.
    miss_reason: str | None = None
    #: The newest reported miss's own instant; ``None`` alongside ``miss_reason``.
    missed_at: str | None = None


class RunnerView(BaseModel):
    """One fleet-registry row — derived liveness, both brakes, and advisory subscription usage.

    The two brakes stay separate: ``hub_paused`` is claims-only, while
    ``locally_paused`` answers "is it spawning at all?". Subscription usage is advisory."""

    runner_id: str
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    hub_paused: bool  # the fleet paused it — `blizzard hub runner pause`, cleared by `hub runner resume`
    locally_paused: bool = False  # it paused itself — spawns nothing, `blizzard runner pause`/`start`
    # The local pause's own cause, populated only alongside a true `locally_paused`;
    # `reason` is `None` for a manual pause.
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    # The configured environment-pool size — ``None`` when none was reported, never zero.
    env_capacity: int | None = None
    # One member per declared subscription; the age-gated fallback without a roster.
    subscriptions: list[SubscriptionUsageView] = []
    # The runner's reported capability snapshot — every harness/tier it can execute right now.
    capabilities: list[RunnerCapability] = []
    # Retired — `blizzard hub runner retire`, cleared by `reinstate`; when and by whom only while retired.
    retired: bool = False
    retired_at: str | None = None
    retired_by: str | None = None


class RunnerListResponse(BaseModel):
    """The fleet registry — every registered runner with its liveness."""

    runners: list[RunnerView] = []


class RunnerPauseRequest(BaseModel):
    """Set a runner's pause brake — records who flipped it."""

    by: str = "operator"


class RunnerRetireRequest(BaseModel):
    """Retire a runner — ``force`` releases every chunk it still holds."""

    by: str = "operator"
    force: bool = False


class RunnerLifecycleRequest(BaseModel):
    """Reinstate a runner, or revoke its token — records who did it."""

    by: str = "operator"


class RunnerRetireResponse(BaseModel):
    """The retired runner, plus every chunk the retire released."""

    runner: RunnerView
    released_chunk_ids: list[str] = []


class RunnerTokenRevocationResponse(BaseModel):
    """The runner after its token was revoked — still registered, now unenrolled."""

    runner: RunnerView
