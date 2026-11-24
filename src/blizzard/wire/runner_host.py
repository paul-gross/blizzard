"""The runner host's own reads — wire bodies for ``/api/host/*`` and the per-runner half of ``/api/ready``: its
runners, its workspaces and their environments, and the subscriptions it declares. Datetimes are ISO-8601 strings with
an explicit UTC offset (``bzh:utc-instants``)."""

from __future__ import annotations

from typing import Annotated, Any

from pydantic import BaseModel, ConfigDict, Field

from blizzard.foundation.host_record_state import HostRecordState
from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.foundation.host_runner_state import HostRunnerState
from blizzard.foundation.runner_start_step import RunnerStartStep
from blizzard.foundation.subscription_miss import SampleMissReason
from blizzard.foundation.unrecognized import Unrecognized
from blizzard.wire.base import WireModel
from blizzard.wire.runner import ExternalSubscriptionUsageWindowView
from blizzard.wire.runner_status import EnvironmentView, PauseStateView, SubscriptionView, ThrottleStatusView
from blizzard.wire.vocabulary import OPEN


class HostRunnerStateView(WireModel):
    """One runner as the host's readiness names it."""

    #: The hub-minted id; ``None`` only for a halted runner configured without one.
    runner_id: str | None
    name: str
    state: Annotated[HostRunnerState | Unrecognized, OPEN]


class HostRunnerBindingView(BaseModel):
    """One project a runner serves, the workspace it runs in, and the agents it runs there at once."""

    project: str
    workspace_id: str
    agents: int


class HostRunnerStartFailureView(BaseModel):
    """Why a runner's latest start failed: the startup step it stopped at and the error."""

    step: Annotated[RunnerStartStep | Unrecognized, OPEN]
    error: str


class HostRunnerView(HostRunnerStateView):
    """One runner on the host: a served runner's brake, bindings and agents in use, or a halted one's reasons.
    ``served`` is whether its routes answer — true for a runner halted by its hub's refusal, whose own views still
    read, false for one halted by its configuration."""

    served: bool = False
    halted_reasons: list[str] = Field(default_factory=list)
    #: ``None`` for a halted runner, which holds no brake.
    pause: PauseStateView | None = None
    throttle: ThrottleStatusView | None = None
    bindings: list[HostRunnerBindingView] = Field(default_factory=lambda: list[HostRunnerBindingView]())
    agents_in_use: int = 0
    #: The sum of its bindings' ``agents``.
    max_agents: int = 0
    #: Why its latest start failed, for a ``start_failed`` runner; ``None`` otherwise.
    start_failure: HostRunnerStartFailureView | None = None


class HostRunnerRetiredView(HostRunnerView):
    """A runner just retired: the chunks its hub released from it, and whether it was retired already, so the retire
    wrote nothing."""

    released_chunk_ids: list[str] = Field(default_factory=list)
    already: bool = False


class HostRunnerListResponse(WireModel):
    items: list[HostRunnerView] = []


class HostRunnerProjectWrite(BaseModel):
    """One project a runner being added serves, and the host's workspace it runs in — the host's only one when
    omitted."""

    project: str
    workspace: str | None = None


class HostRunnerAddRequest(BaseModel):
    """A runner to add to the host: its name, the projects it serves, and any further runner settings by key."""

    name: str
    projects: list[HostRunnerProjectWrite] = Field(min_length=1)
    settings: dict[str, Any] = Field(default_factory=dict[str, Any])


class HostRunnerRetireRequest(BaseModel):
    """A retire of one of the host's runners; ``force`` releases the chunks it holds."""

    force: bool = False


class HostRegistrationView(WireModel):
    """The host's registration at its hub, as its latest report found it: the identity its first answered report
    recorded (``host_id``, ``tenant_id``), the name and runners the hub last answered for it, when, and — short of
    registered — why. ``buffered_facts`` counts the facts waiting on the host's own lane."""

    state: Annotated[HostRegistrationState | Unrecognized, OPEN]
    host_id: str | None = None
    tenant_id: str | None = None
    host_name: str | None = None
    answered_at: str | None = None
    held_runner_ids: list[str] = Field(default_factory=list)
    detail: str | None = None
    buffered_facts: int = 0


class HostWorkspaceView(WireModel):
    """One workspace the host declares, with the environments it may hold at once; on a host keeping records, its
    record's revision, state, and settings — every key of its ``[[workspace]]`` table but ``id``, as stated."""

    workspace_id: str
    max_environments: int
    #: The record's revision, an edit's ``If-Match``; ``None`` on a host keeping no records.
    revision: int | None = None
    state: Annotated[HostRecordState | Unrecognized, OPEN] = "active"
    settings: dict[str, Any] = Field(default_factory=dict[str, Any])


class HostWorkspaceRepoWrite(BaseModel):
    """One ``[[workspace.repo]]``: a repository a workspace's environments clone."""

    model_config = ConfigDict(extra="forbid")

    name: str
    url: str


class _HostRecordFields(BaseModel):
    """A record's settings as a write states them: a field absent stands unchanged, one given takes its whole value,
    and an explicit ``null`` returns it to its default."""

    model_config = ConfigDict(extra="forbid")

    def stated(self) -> tuple[dict[str, Any], frozenset[str]]:
        """The settings set, by their table key, and those returned to their defaults."""
        dumped = self.model_dump(mode="json", exclude_unset=True)
        sets = {name: value for name, value in dumped.items() if value is not None}
        return sets, frozenset(name for name, value in dumped.items() if value is None)


class HostWorkspacePatchRequest(_HostRecordFields):
    """A sparse edit of a workspace record, its keys named as the ``[[workspace]]`` table names them."""

    root: str | None = None
    provider: str | None = None
    envs: list[str] | None = None
    max_environments: int | None = None
    repo: list[HostWorkspaceRepoWrite] | None = None
    base_branch: str | None = None
    prompt: str | None = None
    prompt_file: str | None = None
    prompt_package: str | None = None


class HostWorkspaceCreateRequest(HostWorkspacePatchRequest):
    """A new workspace record: its immutable ``id`` and any of its table's other keys; an absent key takes its
    default."""

    id: str

    def stated(self) -> tuple[dict[str, Any], frozenset[str]]:
        sets, _ = super().stated()
        sets.pop("id", None)
        return sets, frozenset()


class HostWorkspaceListResponse(WireModel):
    items: list[HostWorkspaceView] = []


class HostEnvironmentView(EnvironmentView):
    """One environment of a workspace; a held one names the runner holding it and its chunk's project."""

    runner_id: str | None = None
    #: The holding chunk's project, as the hub names it; ``None`` while free, or until the hub has said.
    project: str | None = None


class HostWorkspaceEnvironmentsView(WireModel):
    workspace_id: str
    items: list[HostEnvironmentView] = []


class HostEnvironmentListResponse(WireModel):
    """Every workspace's environments, held or free, grouped by workspace."""

    workspaces: list[HostWorkspaceEnvironmentsView] = []


class HostSubscriptionView(SubscriptionView):
    """One declared subscription's diagnostics, naming the runners that spend it, with its newest sampled snapshot's
    usage ``windows`` — the hub's own window shape — and that snapshot's ``windows_sampled_at``. A miss never masks
    them: they stand until a newer sample replaces them. Empty, and ``None``, before a first sample."""

    spenders: list[str] = Field(default_factory=list)
    windows: list[ExternalSubscriptionUsageWindowView] = Field(
        default_factory=lambda: list[ExternalSubscriptionUsageWindowView]()
    )
    windows_sampled_at: str | None = None
    #: ``"credential_lapsed"`` when the newest attempt is a miss for that reason; ``None`` otherwise.
    condition: Annotated[SampleMissReason | Unrecognized, OPEN] | None = None
    #: The record's revision, an edit's ``If-Match``; ``None`` on a host keeping no records.
    revision: int | None = None
    state: Annotated[HostRecordState | Unrecognized, OPEN] = "active"
    #: Every key of its ``[[subscription]]`` table but ``slug``, as stated.
    settings: dict[str, Any] = Field(default_factory=dict[str, Any])


class HostSubscriptionListResponse(WireModel):
    items: list[HostSubscriptionView] = []


class _HostSubscriptionSettings(_HostRecordFields):
    """The ``[[subscription]]`` keys a subscription record states beside its ``slug``, ``name`` and ``provider``."""

    credentials_path: str | None = None
    sample_interval_seconds: int | None = None
    usage_api_base: str | None = None


class HostSubscriptionPatchRequest(_HostSubscriptionSettings):
    """A sparse edit of a subscription record, its keys named as the ``[[subscription]]`` table names them; its
    ``slug`` and ``provider`` are immutable, and its required ``name`` refuses ``null``."""

    name: str | None = None


class HostSubscriptionCreateRequest(_HostSubscriptionSettings):
    """A new subscription record: its immutable ``slug`` and ``provider``, its ``name``, and any further key of its
    table."""

    slug: str
    name: str
    provider: str

    def stated(self) -> tuple[dict[str, Any], frozenset[str]]:
        sets, _ = super().stated()
        sets.pop("slug", None)
        return sets, frozenset()
