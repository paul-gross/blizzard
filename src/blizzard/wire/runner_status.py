"""The runner's machine-local status view — wire bodies: identity, pause states, capacities
and hub connectivity; the configured environment pool; open questions; parked escalations
with their literal resume command; and open operator takeovers. Datetimes are ISO-8601
strings with an explicit UTC offset (``bzh:utc-instants``)."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.foundation.credential_renewal import RenewalFailureReason, RenewalResult
from blizzard.foundation.runner_identity_state import IdentityState
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.wire.fleet import FleetSummaryView


class PauseStateView(BaseModel):
    """The pause brake's two independent surfaces, plus their effective OR.

    ``local_reason`` is the local brake's own reason — a usage limit, the spend ceiling, or
    ``None`` on a plain operator pause."""

    local: bool
    hub: bool
    effective: bool
    local_reason: str | None = None


class BindingCapacityView(BaseModel):
    """One binding's agent slots: ``used`` plus ``free`` accounts for its ``agents``. ``project`` is the entry as
    configured; ``project_id`` the project it resolved to, ``None`` while it routes nothing."""

    project: str
    project_id: str | None = None
    workspace_id: str
    agents: int
    used: int
    free: int


class CapacitiesView(BaseModel):
    """Agent-slot accounting per binding, plus the runner-wide count of open leases. A lease whose project or
    workspace is not yet known counts against every binding it may belong to."""

    used: int
    bindings: list[BindingCapacityView] = []


class RoutingConflictView(BaseModel):
    """Two or more configured entries resolving to one project; while any stands, the runner claims nothing."""

    project_id: str
    entries: list[str]


class HubConnectivityView(BaseModel):
    """Hub reachability (derived, not probed) plus the outbound backlog depth.

    ``endpoint`` is the configured hub base URL — connectivity facts, not a probe."""

    endpoint: str
    reachable: bool
    last_contact_at: str | None
    buffer_depth: int


class IdentityVerdictView(BaseModel):
    """Whether the runner's token resolves to the runner its host file configures (``store_runner_id``).

    ``unchecked`` until the hub first answers the check, ``matched`` while it does, ``mismatched``
    while the token names another runner, ``refused`` while the hub refuses the token (unknown, revoked,
    retired) — the runner then drives no lease until a check resolves it again. On a registered host,
    ``not_held`` while the hub does not hold the runner under the host and ``tenant_differs`` while it stands
    in another tenant than the host's halt the runner alone. ``reason`` says why for those last three.
    ``reset_command`` names how to restore the token: the token variable and the configured id."""

    state: IdentityState
    store_runner_id: str | None
    token_runner_id: str | None
    checked_at: str | None
    reset_command: str
    reason: str | None = None


class ServedProjectStatusView(BaseModel):
    """One project the runner serves, as its hub last answered: the ``entry`` as configured — a project id
    or slug — and the project it resolved to. ``resolved`` is false for an entry no project answers to, and
    ``answered`` false for a configured entry the hub has not answered yet; either way ``project_id``,
    ``slug`` and ``name`` are ``None``."""

    entry: str
    project_id: str | None = None
    slug: str | None = None
    name: str | None = None
    resolved: bool
    answered: bool = True


class UnmatchedBindingView(BaseModel):
    """One held binding whose workdir matches no workspace the host declares. It keeps its lease and still
    counts against every binding of the runner."""

    chunk_id: str
    environment_id: str
    workdir: str
    held_since: str


class RunnerStatusView(BaseModel):
    """``GET /api/runner`` — identity, pause states, capacities, hub connectivity, last tick."""

    #: The hub-minted id of the runner's latest successful registration; ``None`` until it first registers.
    runner_id: str | None
    #: The runner's name — its latest registered one, or its configured one before it first registers.
    runner_name: str | None = None
    #: The id of the tenant the runner serves, as its hub last answered; ``None`` until a hub that names it answers.
    tenant_id: str | None = None
    #: That tenant's name, as of the same answer.
    tenant_name: str | None = None
    workspace_id: str
    pause: PauseStateView
    capacities: CapacitiesView
    hub: HubConnectivityView
    last_tick_at: str | None
    #: The node names this runner's own loaded configuration holds for a human decision.
    gates: list[str] = []
    #: The identity verdict; ``None`` from a runner with no loop checking it.
    identity: IdentityVerdictView | None = None
    #: The projects the runner serves, read from its store with no hub call; ``None`` when neither its
    #: configuration nor its hub has named any.
    served_projects: list[ServedProjectStatusView] | None = None
    #: Each held binding no workspace has claimed; empty when every held binding names one.
    unmatched_bindings: list[UnmatchedBindingView] = []
    #: Each project two or more bindings resolve to; empty when the routing is sound.
    routing_conflicts: list[RoutingConflictView] = []


class EnvironmentView(BaseModel):
    """One environment in a workspace's pool, as ``GET /api/host/environments`` lists it.
    ``chunk_id``/``held_since`` are present only while the environment
    is currently bound; an unused pool environment carries both as ``None``. ``held`` is the
    runner's own reading of whether a chunk holds it, so no client re-derives it."""

    environment_id: str
    chunk_id: str | None = None
    held_since: str | None = None
    held: bool = False


class AskView(BaseModel):
    """One open ask — ``GET /api/asks?open=true``."""

    question_id: str
    chunk_id: str
    lease_id: str
    question: str
    options: list[str] = []
    session_id: str | None
    harness_id: str | None = None
    asked_at: str


class AskListResponse(BaseModel):
    """Every ask still awaiting an answer."""

    items: list[AskView] = []


class EscalationView(BaseModel):
    """One parked escalation, carrying its literal takeover command — ``GET /api/escalations``."""

    chunk_id: str
    lease_id: str
    node_id: str
    epoch: int
    closed_at: str
    resume_command: str
    # The parked session's own configuration — the pool it belongs to and the model and
    # effort it ran under. All `None` for a bare-vocabulary session, or one predating the stamps.
    session_name: str | None = None
    model: str | None = None
    effort: str | None = None
    harness_id: str | None = None
    #: The escalated generation's own recorded harness build version,
    #: beside ``harness_id``. ``None`` when the generation recorded none.
    harness_version: str | None = None
    #: The ``blizzard runner takeover`` invocation. ``None`` when the runner cannot compose it.
    #: Holds only the chunk id and runner dir.
    wrapped_takeover_command: str | None = None
    cause: str | None = None


class EscalationListResponse(BaseModel):
    """Every escalation still open — no later lease mint, and the hub has not ended the chunk."""

    items: list[EscalationView] = []


class OpenTakeoverView(BaseModel):
    """One open operator takeover — ``GET /api/takeovers``, the stranded-takeover
    recovery surface: the chunk it holds, the ``takeover_id`` an
    interrupted terminal never PATCHed closed, and how long it has been held."""

    chunk_id: str
    takeover_id: str
    held_since: str
    harness_id: str | None = None


class OpenTakeoverListResponse(BaseModel):
    """Every takeover still open across this runner's held chunks."""

    items: list[OpenTakeoverView] = []


class FactView(BaseModel):
    """One hub-bound fact off the runner store's outbound buffer — ``GET /api/facts``.

    The local fact log: the record itself minus its JSON ``payload``. ``acked_at``
    null means still buffered."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: str
    acked_at: str | None


class FactListResponse(BaseModel):
    """The most recent hub-bound facts, newest first."""

    items: list[FactView] = []


class HarnessHealthView(BaseModel):
    """One configured harness binding's own computed health —
    ``GET /api/harness-health``, runner-local diagnostics only. ``cause`` is one of
    ``missing_binary``, ``incompatible_version``, ``unknown_version``, ``authentication_failure``,
    ``config_conflict``, ``unmapped_tier``, or ``selftest_failure`` when unavailable; ``declared_degradation`` when
    available but degraded; ``None`` only when available with no declared degradation either."""

    harness_id: str
    #: Normalized when the binding's raw shape allows it.
    version: str | None = None
    available: bool
    cause: str | None = None
    degradations: list[str] = []
    #: The literal display string of the declared admitted-version range; ``None`` when none.
    admitted_range: str | None = None


class HarnessHealthListResponse(BaseModel):
    """Every configured harness binding's own computed health."""

    items: list[HarnessHealthView] = []


class SubscriptionView(BaseModel):
    """One declared subscription's host-local diagnostics, served by
    ``GET /api/host/subscriptions``. The sampling fields are ``None`` until a first attempt, the
    ``renewal_*`` fields until a first renewal; each ``*_reason`` is set only on a miss or failure."""

    slug: str
    name: str
    provider: str
    sampled_at: str | None = None
    ok: bool | None = None
    miss_reason: SampleMissReason | None = None
    renewal_attempted_at: str | None = None
    renewal_result: RenewalResult | None = None
    renewal_failure_reason: RenewalFailureReason | None = None


class DashboardView(BaseModel):
    """``GET /runners/{runner_id}/api/dashboard`` — seven status reads composed into one response.
    ``fleet_summary`` alone is a hub pass-through and the only nullable section —
    ``None`` on a hub failure or an unwired runner, while the six local sections
    still populate. The host's environments and subscriptions are its own reads, under ``/api/host``."""

    runner: RunnerStatusView
    asks: AskListResponse
    escalations: EscalationListResponse
    takeovers: OpenTakeoverListResponse
    facts: FactListResponse
    harness_health: HarnessHealthListResponse
    fleet_summary: FleetSummaryView | None
