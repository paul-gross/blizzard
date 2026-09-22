"""The runner's machine-local status view — wire bodies (issue #51): identity, pause states, capacities
and hub connectivity; the configured environment pool (issue #106); open questions; parked escalations
with their literal resume command; and open operator takeovers (issue #52). Datetimes are ISO-8601
strings with an explicit UTC offset (``bzh:utc-instants``)."""

from __future__ import annotations

from pydantic import BaseModel

from blizzard.wire.fleet import FleetSummaryView


class PauseStateView(BaseModel):
    """The pause brake's two independent surfaces, plus their effective OR.

    ``local_reason`` is the local brake's own reason — a usage limit, the spend ceiling, or
    ``None`` on a plain operator pause — the runner-local mirror of the reason the hub already
    shows for a runner's local pause (blizzard#594)."""

    local: bool
    hub: bool
    effective: bool
    local_reason: str | None = None


class CapacitiesView(BaseModel):
    """Agent-slot accounting: ``used`` plus ``free`` accounts for ``max_agents``."""

    max_agents: int
    used: int
    free: int


class HubConnectivityView(BaseModel):
    """Hub reachability (derived, not probed) plus the outbound backlog depth.

    ``endpoint`` is the configured hub base URL — connectivity facts, not a probe."""

    endpoint: str
    reachable: bool
    last_contact_at: str | None
    buffer_depth: int


class RunnerStatusView(BaseModel):
    """``GET /api/runner`` — identity, pause states, capacities, hub connectivity, last tick."""

    runner_id: str
    workspace_id: str
    pause: PauseStateView
    capacities: CapacitiesView
    hub: HubConnectivityView
    last_tick_at: str | None


class EnvironmentView(BaseModel):
    """One environment in the runner's configured pool — ``GET /api/environments``
    (issue #106). ``chunk_id``/``held_since`` are present only while the environment
    is currently bound; an unused pool environment carries both as ``None``."""

    environment_id: str
    chunk_id: str | None = None
    held_since: str | None = None


class EnvironmentListResponse(BaseModel):
    """Every environment in the runner's configured pool, held or free."""

    items: list[EnvironmentView] = []


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
    # The parked session's own configuration (issue #144) — the pool it belongs to and the model and
    # effort it ran under. All `None` for a bare-vocabulary session, or one predating the stamps.
    session_name: str | None = None
    model: str | None = None
    effort: str | None = None
    harness_id: str | None = None
    #: The escalated generation's own recorded harness build version (blizzard#441),
    #: beside ``harness_id``. ``None`` when the generation recorded none.
    harness_version: str | None = None


class EscalationListResponse(BaseModel):
    """Every escalation still open — no later lease mint, and the hub has not ended the chunk."""

    items: list[EscalationView] = []


class OpenTakeoverView(BaseModel):
    """One open operator takeover — ``GET /api/takeovers``, the stranded-takeover
    recovery surface (issue #52): the chunk it holds, the ``takeover_id`` an
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
    """One configured harness binding's own computed health (blizzard#438) —
    ``GET /api/harness-health``, runner-local diagnostics only. ``cause`` is one of
    ``missing_binary``, ``incompatible_version``, ``unknown_version``, ``authentication_failure``,
    ``unmapped_tier``, or ``selftest_failure`` when unavailable; ``declared_degradation`` when
    available but degraded; ``None`` only when available with no declared degradation either."""

    harness_id: str
    #: Normalized when the binding's raw shape allows it, so it never looks like a non-member below.
    version: str | None = None
    available: bool
    cause: str | None = None
    degradations: list[str] = []
    #: This binding's own declared admitted-version range as its literal display string (D3); ``None`` when none.
    admitted_range: str | None = None


class HarnessHealthListResponse(BaseModel):
    """Every configured harness binding's own computed health."""

    items: list[HarnessHealthView] = []


class SubscriptionView(BaseModel):
    """One declared subscription's own runner-local diagnostics (blizzard#504) —
    ``GET /api/subscriptions``. ``sampled_at``/``ok``/``miss_reason``/``renewal`` are all
    ``None`` when this slug has never been attempted. ``miss_reason`` is one of
    ``credential_lapsed``, ``credential_unreadable``, ``endpoint_unreachable``, or
    ``response_unparseable`` when ``ok`` is ``False``; ``None`` when ``ok`` is ``True``.
    ``renewal`` is ``None`` until a renewer is wired for this slug's provider."""

    slug: str
    name: str
    provider: str
    sampled_at: str | None = None
    ok: bool | None = None
    miss_reason: str | None = None
    renewal: str | None = None


class SubscriptionListResponse(BaseModel):
    """Every declared subscription's own newest sampling attempt."""

    items: list[SubscriptionView] = []


class DashboardView(BaseModel):
    """``GET /api/dashboard`` — nine status reads composed into one response.
    ``fleet_summary`` alone is a hub pass-through and the only nullable section —
    ``None`` on a hub failure or an unwired runner, while the eight local sections
    still populate."""

    runner: RunnerStatusView
    environments: EnvironmentListResponse
    asks: AskListResponse
    escalations: EscalationListResponse
    takeovers: OpenTakeoverListResponse
    facts: FactListResponse
    harness_health: HarnessHealthListResponse
    subscriptions: SubscriptionListResponse
    fleet_summary: FleetSummaryView | None
