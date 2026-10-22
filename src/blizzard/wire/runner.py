"""Fleet-registry wire bodies.

A runner's ``runner_id`` is the hub-minted ``rn_`` id, its one identity; ``runner_name`` is the
display name the runner declares, which nothing keys on. ``online``, ``connection`` and ``paused``
are **derived**. :class:`RunnerAddResponse` and :class:`RunnerEnrollmentResponse` are the only
bodies that ever carry a runner's plaintext bearer token."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.runner_tokens import RunnerTokenRefusalReason
from blizzard.foundation.runner_version import RunnerVersionStanding, VersionRefusalReason
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.vocabulary import OPEN


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


class ServedProjectView(WireModel):
    """One entry of the projects a runner serves: the ``entry`` as declared — a project id or slug — and the project
    it resolves to in the runner's tenant, with its palette ``color`` — a palette name or a ``#rrggbb``.
    ``project_id``, ``slug``, ``name`` and ``color`` are ``None`` for an entry that resolves to no project, which
    serves nothing until a project answers to it. ``claimable``, set on the runners read, is the hub's judgment that the
    runner can claim the project now."""

    entry: str
    project_id: str | None = None
    slug: str | None = None
    name: str | None = None
    color: str | None = None
    claimable: bool = False


class RunnerBinding(BaseModel):
    """One project a runner serves through one of its host's workspaces: the ``project`` by id or slug, the host
    ``workspace_id`` it works in, and the ``agents`` it runs there at once."""

    project: str
    workspace_id: str
    agents: int


class RunnerBindingView(WireModel):
    """A runner's binding as the hub holds it. ``workspace_unreported`` is derived on read: the runner's host has not
    reported the workspace, so the hub offers the runner no work through the binding."""

    project: str
    workspace_id: str
    agents: int
    workspace_unreported: bool = False


class RunnerDeclining(BaseModel):
    """One throttle the runner reports as saying no to new work — the reason, never a setting. ``lever`` names the
    throttle and stays a plain string so a lever a newer runner adds never fails an older hub's parse; ``detail`` is the
    reading alone, ``subscription`` the subscription it concerns (``None`` when it names none) and ``until`` the instant
    the hold expects to lift (``None`` when it names none)."""

    lever: str
    detail: str
    subscription: str | None = None
    until: datetime | None = None


class RunnerDecliningView(WireModel):
    """One throttle a runner reported as saying no, as the hub holds it: the response-side shape of the registration
    request's ``declining`` member."""

    lever: str
    detail: str
    subscription: str | None = None
    until: str | None = None


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
    #: The projects the runner serves, each by id or slug — ``None`` keeps the declaration the hub holds, ``[]``
    #: serves none.
    projects: list[str] | None = None
    #: The projects the runner serves through its host's workspaces. Sent, it replaces ``projects`` as the served
    #: declaration, in binding order (``[]`` serves none); ``None`` leaves ``projects`` in charge. Stored as sent,
    #: ``None`` included, on every registration.
    bindings: list[RunnerBinding] | None = None
    #: The slug of the host subscription every binding spends; ``None`` names none.
    subscription: str | None = None
    #: The agents the runner runs at once, shared by every binding; positive when set, ``None`` from a runner that
    #: predates the shared count.
    agents: int | None = Field(default=None, gt=0)
    #: The blizzard version this runner runs; ``None`` from a runner that predates the handshake.
    version: str | None = None
    #: The throttles saying no to new work right now, one member per throttle — ``[]`` when none is (or the runner is
    #: braked), ``None`` from a runner that predates the report. Supersedes the earlier report whole.
    declining: list[RunnerDeclining] | None = None


class VersionWindowView(BaseModel):
    """The hub's compatibility window: the oldest major.minor line it admits, and the newest, which is its own."""

    floor: str
    ceiling: str


class RunnerVersionRefusal(WireModel):
    """The hub's window refusing a runner's version."""

    reason: Annotated[VersionRefusalReason | Unrecognized, OPEN]
    #: The version the runner reported; ``None`` when it reported none, or one that does not parse.
    runner_version: str | None = None
    hub_version: str
    floor: str
    #: A sentence naming what to upgrade, and to which range.
    detail: str


class RunnerRegistrationResponse(WireModel):
    """The registered runner's hub-minted id and current name, whether this call was its first
    registration since it was added, and the tenant it serves. Registration doubles as the
    heartbeat, so a renamed tenant reaches the runner on its next tick."""

    runner_id: str
    #: The name the registry now holds for this runner.
    runner_name: str | None = None
    first_registration: bool
    #: The tenant the runner was added in — the one its bearer token resolves to. The hub always
    #: fills both; ``None`` only from a hub that predates tenants.
    tenant_id: str | None = None
    #: That tenant's current name.
    tenant_name: str | None = None
    #: The projects the runner serves, as the hub resolved its declaration; ``None`` from a hub that predates
    #: projects.
    projects: list[ServedProjectView] | None = None
    #: The bindings the hub now holds, each flagged against the host's latest report; ``None`` when none were sent.
    bindings: list[RunnerBindingView] | None = None
    subscription: str | None = None
    #: Whether the host's roster lacks ``subscription`` — the hub then offers the runner no work.
    subscription_unrostered: bool = False
    #: The hub's own version; ``None`` from a hub that predates the handshake.
    hub_version: str | None = None
    #: The window the hub judged this runner's version under; ``None`` from a hub that predates the handshake.
    window: VersionWindowView | None = None


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
    #: The projects the runner serves, each by id or slug — ``None`` serves the tenant's project when it holds exactly
    #: one, and none otherwise; ``[]`` serves none. An entry naming no project is refused (422).
    projects: list[str] | None = None
    #: The host the runner is added under — it stands under that host for good, until released. ``None`` adds a
    #: runner that stands alone. An unknown or retired host is refused.
    host_id: str | None = None


class RunnerAddResponse(BaseModel):
    """The added runner — its hub-minted id, its initial name, and its plaintext bearer token.

    ``token`` is visible only here, once — only its sha256 hash is kept. The runner is added but
    never connected until it first registers with this token."""

    runner_id: str
    runner_name: str
    token: str


class RunnerIdentityView(WireModel):
    """Who the presented runner bearer token belongs to — its hub-minted id and current name, and
    the tenant it was added in, and the hub's ``fleet`` flags. Answering records nothing: no registration, no
    liveness."""

    runner_id: str
    runner_name: str
    #: The runner's tenant, read as registration reads it. The hub always fills both; ``None``
    #: only from a hub that predates tenants.
    tenant_id: str | None = None
    #: That tenant's current name.
    tenant_name: str | None = None
    #: Every live ``fleet`` flag at the hub's resolved value, and every retired one on; empty from a hub that predates
    #: them. A runner applies it only once the answer names the runner it is configured as.
    fleet_flags: dict[str, bool] = {}


class RunnerIdentityRefusal(WireModel):
    """Why the presented runner bearer token names no runner the hub admits. ``runner_id`` names
    the runner a ``revoked`` or ``retired`` token was issued to, and is ``None`` for a ``missing``
    or ``unknown`` one."""

    reason: Annotated[RunnerTokenRefusalReason | Unrecognized, OPEN]
    runner_id: str | None = None


class ExternalSubscriptionUsageWindowView(BaseModel):
    """One rate-limit window's utilization, as the harness's own account reported it
    — ``window`` is the harness-native label, ``utilization_pct`` is 0-100,
    ``resets_at`` the reset instant, ``window_seconds`` the window's length."""

    window: str
    utilization_pct: float
    resets_at: str
    window_seconds: int


class SubscriptionUsageView(WireModel):
    """One reported subscription's newest sampled usage, carrying its identity.
    ``slug`` is the runner-unique join key, ``name`` the operator-facing
    label. ``sampled_at`` is ``None`` for a miss-only row — ``condition``
    carries the reason in that case, and ``windows`` is empty."""

    slug: str
    name: str
    sampled_at: str | None = None
    windows: list[ExternalSubscriptionUsageWindowView]
    #: ``"credential_lapsed"`` when the newest reported miss outranks the newest sample; ``None`` otherwise.
    condition: Annotated[SampleMissReason | Unrecognized, OPEN] | None = None
    #: The newest reported miss's own reason; ``None`` when there is none.
    miss_reason: Annotated[SampleMissReason | Unrecognized, OPEN] | None = None
    #: The newest reported miss's own instant; ``None`` alongside ``miss_reason``.
    missed_at: str | None = None


class RunnerView(WireModel):
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
    # The agents the runner declared it runs at once; `None` from a runner that predates the shared count.
    agents: int | None = None
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
    # The projects the runner serves, as declared and resolved; empty when it serves none.
    projects: list[ServedProjectView] = []
    # The bindings the runner declared, flagged against its host's latest report; `None` when it declared none.
    bindings: list[RunnerBindingView] | None = None
    subscription: str | None = None
    subscription_unrostered: bool = False
    # The throttles the runner last reported as saying no; empty when none, or when it sent none.
    declining: list[RunnerDecliningView] = []


class RunnerRegistryView(WireModel):
    """One runner as an operator sees the registry — including one that has never connected.

    A ``never_connected`` runner has no ``workspace_id``, ``registered_at`` or ``last_seen_at``, no
    capabilities, and is not ``online``."""

    runner_id: str
    # The runner's latest display name — not unique, never a key.
    runner_name: str
    connection: Annotated[RunnerConnection | Unrecognized, OPEN]
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
    # The agents the runner declared it runs at once; `None` from a runner that predates the shared count.
    agents: int | None = None
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
    # The projects the runner serves, as declared and resolved; empty when it serves none.
    projects: list[ServedProjectView] = []
    # The bindings the runner declared, flagged against its host's latest report; `None` when it declared none.
    bindings: list[RunnerBindingView] | None = None
    subscription: str | None = None
    subscription_unrostered: bool = False
    # The host the runner stands under; `None` for a runner that stands alone.
    host_id: str | None = None
    # The version the runner's latest registration reported; `None` when it reported none.
    version: str | None = None
    # Where that registration stands against the hub's window; `None` for a runner never registered.
    version_standing: Annotated[RunnerVersionStanding | Unrecognized, OPEN] | None = None
    # Why the window refused it, alongside a `refused` standing only.
    version_refusal: Annotated[VersionRefusalReason | Unrecognized, OPEN] | None = None
    # The throttles the runner last reported as saying no; empty when none, or when it sent none.
    declining: list[RunnerDecliningView] = []


class RunnerRegistryListResponse(WireModel):
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


class RunnerRetireResponse(WireModel):
    """The retired runner, plus every chunk the retire released."""

    runner: RunnerRegistryView
    released_chunk_ids: list[str] = []


class RunnerTokenRevocationResponse(BaseModel):
    """The runner after its token was revoked — still added, now unenrolled."""

    runner: RunnerRegistryView
