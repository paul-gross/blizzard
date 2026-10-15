"""The runner host's own reads — wire bodies for ``/api/host/*`` and the per-runner half of ``/api/ready``: its
runners, its workspaces and their environments, and the subscriptions it declares. Datetimes are ISO-8601 strings with
an explicit UTC offset (``bzh:utc-instants``)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, Field

from blizzard.wire.runner_status import EnvironmentView, PauseStateView, SubscriptionView

#: Whether the host serves a runner, or holds it halted — by its own configuration errors, or, while served, by a hub
#: that refuses its token or does not hold it under the host.
HostRunnerState = Literal["serving", "halted"]

#: Where the host stands with its hub: ``unregistered``, holding no host token; ``pending``, holding one and not yet
#: reported; ``registered``, the hub answering its latest report as this host; ``refused``, the hub refusing its token
#: or answering as another host; ``no_hosts``, a hub from before hosts; ``unreachable``, its latest report unanswered.
HostRegistrationState = Literal["unregistered", "pending", "registered", "refused", "no_hosts", "unreachable"]


class HostRunnerStateView(BaseModel):
    """One runner as the host's readiness names it."""

    #: The hub-minted id; ``None`` only for a halted runner configured without one.
    runner_id: str | None
    name: str
    state: HostRunnerState


class HostRunnerBindingView(BaseModel):
    """One project a runner serves, the workspace it runs in, and the agents it runs there at once."""

    project: str
    workspace_id: str
    agents: int


class HostRunnerView(HostRunnerStateView):
    """One runner on the host: a served runner's brake, bindings and agents in use, or a halted one's reasons.
    ``served`` is whether its routes answer — true for a runner halted by its hub's refusal, whose own views still
    read, false for one halted by its configuration."""

    served: bool = False
    halted_reasons: list[str] = Field(default_factory=list)
    #: ``None`` for a halted runner, which holds no brake.
    pause: PauseStateView | None = None
    bindings: list[HostRunnerBindingView] = Field(default_factory=list)
    agents_in_use: int = 0
    #: The sum of its bindings' ``agents``.
    max_agents: int = 0


class HostRunnerListResponse(BaseModel):
    items: list[HostRunnerView] = []


class HostRegistrationView(BaseModel):
    """The host's registration at its hub, as its latest report found it: the identity its first answered report
    recorded (``host_id``, ``tenant_id``), the name and runners the hub last answered for it, when, and — short of
    registered — why. ``buffered_facts`` counts the facts waiting on the host's own lane."""

    state: HostRegistrationState
    host_id: str | None = None
    tenant_id: str | None = None
    host_name: str | None = None
    answered_at: str | None = None
    held_runner_ids: list[str] = Field(default_factory=list)
    detail: str | None = None
    buffered_facts: int = 0


class HostWorkspaceView(BaseModel):
    """One workspace the host declares, with the environments it may hold at once."""

    workspace_id: str
    max_environments: int


class HostWorkspaceListResponse(BaseModel):
    items: list[HostWorkspaceView] = []


class HostEnvironmentView(EnvironmentView):
    """One environment of a workspace; a held one names the runner holding it and its chunk's project."""

    runner_id: str | None = None
    #: The holding chunk's project, as the hub names it; ``None`` while free, or until the hub has said.
    project: str | None = None


class HostWorkspaceEnvironmentsView(BaseModel):
    workspace_id: str
    items: list[HostEnvironmentView] = []


class HostEnvironmentListResponse(BaseModel):
    """Every workspace's environments, held or free, grouped by workspace."""

    workspaces: list[HostWorkspaceEnvironmentsView] = []


class HostSubscriptionView(SubscriptionView):
    """One declared subscription's diagnostics, naming the runners that spend it."""

    spenders: list[str] = Field(default_factory=list)


class HostSubscriptionListResponse(BaseModel):
    items: list[HostSubscriptionView] = []
