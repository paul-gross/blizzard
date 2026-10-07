"""Fleet-registry wire bodies.

A runner's ``runner_id`` is the hub-minted ``rn_`` id, its one identity; ``runner_name`` is the
display name the runner declares, which nothing keys on. ``online``, ``connection`` and ``paused``
are **derived**. :class:`RunnerAddResponse` and :class:`RunnerEnrollmentResponse` are the only
bodies that ever carry a runner's plaintext bearer token."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, StringConstraints

from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.subscription_miss import SampleMissReason


class RunnerCapability(BaseModel):
    """One harness binding this runner can execute — the id, its observed
    version (``None`` when the binding exposes none), the tier ids it can resolve, and
    whether it is this runner's default binding. ``available`` defaults
    ``True``: a binding that does not state otherwise is available. A runner reporting no bindings at all is
    eligible for nothing."""

    harness_id: str
    version: str | None = None
    tiers: list[str] = []
    default: bool = False
    available: bool = True


class RunnerSubscriptionDeclaration(BaseModel):
    """One provider subscription the runner declares at registration — the join key
    everything else keys off. ``provider`` names the subscription's provider."""

    slug: str
    name: str
    provider: str


class RunnerRegistrationRequest(BaseModel):
    """Register the calling runner — the one its bearer token was issued to; a stray ``runner_id``
    in the body is ignored. ``env_capacity`` is the runner's configured environment-pool size,
    ``None`` when the client reports none, never a guessed total."""

    #: The runner's display name — not unique, never a key; ``None`` or blank keeps the one the registry holds.
    name: str | None = None
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
    #: The node names this runner holds for a human decision — its own configuration, reported not enforced by the hub.
    gates: list[str] = []


class RunnerRegistrationResponse(BaseModel):
    """The registered runner's hub-minted id and current name, and whether this call was its
    first registration since it was added."""

    runner_id: str
    #: The name the registry now holds for this runner.
    runner_name: str | None = None
    first_registration: bool


class RunnerEnrollmentResponse(BaseModel):
    """A rotated bearer token for an existing runner id.

    ``token`` is the plaintext, visible only here — only its sha256 hash is kept. The old
    token stops resolving the moment this response lands."""

    runner_id: str
    token: str


class RunnerAddRequest(BaseModel):
    """Add a runner under an initial display name. The hub mints the runner's id and bearer
    token together; the runner's own registrations set its name from then on."""

    #: A blank name is refused (422).
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]


class RunnerAddResponse(BaseModel):
    """The added runner — its hub-minted id, its initial name, and its plaintext bearer token.

    ``token`` is visible only here, once — only its sha256 hash is kept. The runner is added but
    never connected until it first registers with this token."""

    runner_id: str
    runner_name: str
    token: str


class RunnerIdentityView(BaseModel):
    """Who the presented runner bearer token belongs to — its hub-minted id and current name.
    Answering records nothing: no registration, no liveness."""

    runner_id: str
    runner_name: str


class RunnerIdentityRefusal(BaseModel):
    """Why the presented runner bearer token names no runner the hub admits. ``runner_id`` names
    the runner a ``revoked`` or ``retired`` token was issued to, and is ``None`` for a ``missing``
    or ``unknown`` one."""

    reason: RunnerTokenRefusalReason
    runner_id: str | None = None


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
    condition: SampleMissReason | None = None
    #: The newest reported miss's own reason; ``None`` when there is none.
    miss_reason: SampleMissReason | None = None
    #: The newest reported miss's own instant; ``None`` alongside ``miss_reason``.
    missed_at: str | None = None


class RunnerView(BaseModel):
    """A registered runner's own view of its registration.

    The two brakes stay separate: ``hub_paused`` is claims-only, while
    ``locally_paused`` answers "is it spawning at all?". Subscription usage is advisory."""

    runner_id: str
    # The runner's latest registered display name — not unique, never a key.
    runner_name: str | None = None
    workspace_id: str
    registered_at: str
    last_seen_at: str
    online: bool
    hub_paused: bool  # the fleet paused it — the hub routes it no new claims
    locally_paused: bool = False  # it paused itself — it spawns nothing
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
    # Whether the runner is retired; when and by whom only while retired.
    retired: bool = False
    retired_at: str | None = None
    retired_by: str | None = None
    # The node names the runner declared it holds for a human decision; empty when it imposes none.
    gates: list[str] = []


class RunnerRegistryView(BaseModel):
    """One runner as an operator sees the registry — including one that has never connected.

    A ``never_connected`` runner has no ``workspace_id``, ``registered_at`` or ``last_seen_at``, no
    capabilities, and is not ``online``."""

    runner_id: str
    # The runner's latest display name — not unique, never a key.
    runner_name: str
    connection: RunnerConnection
    online: bool
    # When the runner was added, and by whom; `added_by` is `None` when the hub did not record one.
    added_at: str
    added_by: str | None = None
    workspace_id: str | None = None
    registered_at: str | None = None
    last_seen_at: str | None = None
    hub_paused: bool  # the fleet paused it — the hub routes it no new claims
    locally_paused: bool = False  # it paused itself — it spawns nothing
    # The local pause's own cause; `reason` is `None` for a manual pause.
    locally_paused_by: str | None = None
    locally_paused_reason: str | None = None
    # The configured environment-pool size — ``None`` when none was reported, never zero.
    env_capacity: int | None = None
    # One member per declared subscription; the age-gated fallback without a roster.
    subscriptions: list[SubscriptionUsageView] = []
    # The runner's reported capability snapshot — every harness/tier it can execute right now.
    capabilities: list[RunnerCapability] = []
    # Whether the runner is retired; when and by whom only while retired.
    retired: bool = False
    retired_at: str | None = None
    retired_by: str | None = None
    # The node names the runner declared it holds for a human decision; empty when it imposes none.
    gates: list[str] = []


class RunnerRegistryListResponse(BaseModel):
    """The fleet registry as operators list it — every added runner, oldest first, each with its
    connection condition. Two runners may share a name; each is listed under its own id."""

    runners: list[RunnerRegistryView] = []


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

    runner: RunnerRegistryView
    released_chunk_ids: list[str] = []


class RunnerTokenRevocationResponse(BaseModel):
    """The runner after its token was revoked — still added, now unenrolled."""

    runner: RunnerRegistryView
