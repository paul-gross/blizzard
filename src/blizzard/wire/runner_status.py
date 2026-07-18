"""The runner's machine-local status view — wire bodies (issue #51).

Behind ``GET /api/runner`` (identity, pause states, capacities, hub connectivity,
last tick), ``GET /api/environments`` (held bindings), ``GET /api/asks?open=true``
(open questions), ``GET /api/escalations`` (parked escalations with their
literal resume command), and ``GET /api/takeovers`` (open operator takeovers —
the stranded-takeover recovery surface, issue #52) — ``blizzard runner status``'s
five reads. Modeled on ``wire/lease.py``'s and ``wire/runner.py``'s shapes;
datetimes are ISO-8601 strings with an explicit UTC offset, serialized via
``foundation/store/utc.py``'s ``iso_utc`` (``bzh:utc-instants``).
"""

from __future__ import annotations

from pydantic import BaseModel


class PauseStateView(BaseModel):
    """The pause brake's two independent surfaces, plus their effective OR."""

    local: bool
    hub: bool
    effective: bool


class CapacitiesView(BaseModel):
    """Agent slots — the same math FILL claims against."""

    max_agents: int
    used: int
    free: int


class HubConnectivityView(BaseModel):
    """Hub reachability (derived, not probed) plus the outbound backlog depth.

    ``endpoint`` is the configured hub base URL (``RunnerConfig.hub_url``) — the
    local panel's link out to the fleet board; connectivity facts, not a probe."""

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


class HeldEnvironmentView(BaseModel):
    """One environment this runner currently holds — ``GET /api/environments``."""

    environment_id: str
    chunk_id: str
    held_since: str


class EnvironmentListResponse(BaseModel):
    """Every environment this runner currently holds."""

    items: list[HeldEnvironmentView] = []


class AskView(BaseModel):
    """One open ask — ``GET /api/asks?open=true``."""

    question_id: str
    chunk_id: str
    lease_id: str
    question: str
    options: list[str] = []
    session_id: str | None
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


class EscalationListResponse(BaseModel):
    """Every escalation still open — no later lease mint has superseded it."""

    items: list[EscalationView] = []


class OpenTakeoverView(BaseModel):
    """One open operator takeover — ``GET /api/takeovers``, the stranded-takeover
    recovery surface (issue #52): the chunk it holds, the ``takeover_id`` an
    interrupted terminal never PATCHed closed, and how long it has been held."""

    chunk_id: str
    takeover_id: str
    held_since: str


class OpenTakeoverListResponse(BaseModel):
    """Every takeover still open across this runner's held chunks."""

    items: list[OpenTakeoverView] = []


class FactView(BaseModel):
    """One hub-bound fact off the runner store's outbound buffer — ``GET /api/facts``.

    The local fact log: the record itself minus its JSON ``payload`` (the panel
    reads the ledger, not the bodies). ``acked_at`` null means still buffered."""

    seq: int
    kind: str
    chunk_id: str | None
    lease_id: str | None
    created_at: str
    acked_at: str | None


class FactListResponse(BaseModel):
    """The most recent hub-bound facts, newest first."""

    items: list[FactView] = []
