"""Host wire bodies.

A host's ``host_id`` is the hub-minted ``hs_`` id, its one identity; ``host_name`` is the operator's label, which a
report may replace. ``connection`` and ``online`` are **derived**. :class:`HostAddResponse` and
:class:`HostEnrollmentResponse` are the only bodies that ever carry a host's plaintext bearer token."""

from __future__ import annotations

from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.wire.base import WireModel
from blizzard.wire.runner import RunnerSubscriptionDeclaration, SubscriptionUsageView


class HostWorkspace(BaseModel):
    """One workspace a host reports: its id and how many environments it holds, ``None`` when it reports none."""

    workspace_id: str
    env_capacity: int | None = None


class HostReportRequest(BaseModel):
    """What the calling host reports about itself on every tick — the host its bearer token was issued to. The
    ``workspaces`` and ``subscriptions`` replace what the hub holds, whole. A report is never refused over its
    content: a ``name`` that is blank, or that another live host holds, leaves the held name."""

    name: str | None = None
    url: str | None = None
    redirect_uris: list[str] = []
    workspaces: list[HostWorkspace] = []
    subscriptions: list[RunnerSubscriptionDeclaration] = []
    #: The blizzard version the host runs; ``None`` from a host that predates the handshake.
    version: str | None = None


class HostRunnerView(WireModel):
    """One runner standing under a host."""

    runner_id: str
    runner_name: str


class HostRecordResponse(WireModel):
    """A host's own record: its id, tenant, held name, and the runners the hub holds under it that are not retired —
    a runner retired or released is one the host should halt."""

    host_id: str
    tenant_id: str
    host_name: str
    registered_at: str | None = None
    last_seen_at: str | None = None
    runners: list[HostRunnerView] = []


class HostRunnerAddRequest(BaseModel):
    """Add a runner under the calling host, under an initial display name — as ``RunnerAddRequest`` adds one, with the
    host fixed to the caller. ``projects`` is what the runner serves until a registration declares otherwise."""

    #: A blank name is refused (422).
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    projects: list[str] | None = None


class HostAddRequest(BaseModel):
    """Add a host under a name unique among the tenant's live hosts. The hub mints the host's id and bearer token
    together; ``adopt`` places existing runners under it in the same write."""

    #: A blank name is refused (422).
    name: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1)]
    adopt: list[str] = []


class HostAddResponse(WireModel):
    """The added host — its hub-minted id, its name, and its plaintext bearer token.

    ``token`` is visible only here, once — only its sha256 hash is kept. The host is added but never connected until
    it first reports with this token."""

    host_id: str
    host_name: str
    token: str
    adopted: list[str] = []


class HostSubscriptionUsageView(SubscriptionUsageView):
    """One of a host's subscriptions with its newest reported usage — as a runner's card shows it — and the runners
    spending it."""

    #: The ids of the live runners under the host whose registered subscription is this slug.
    runners: list[str] = Field(default_factory=list)


class HostRegistryView(WireModel):
    """One host as an operator sees the registry — including one that has never connected."""

    host_id: str
    host_name: str
    connection: RunnerConnection
    online: bool
    added_at: str
    added_by: str | None = None
    registered_at: str | None = None
    last_seen_at: str | None = None
    url: str | None = None
    redirect_uris: list[str] = []
    workspaces: list[HostWorkspace] = []
    subscriptions: list[RunnerSubscriptionDeclaration] = []
    #: Whether the host holds a bearer token.
    enrolled: bool
    retired: bool = False
    retired_at: str | None = None
    retired_by: str | None = None
    #: The runners standing under the host, retired ones included, oldest added first.
    runner_ids: list[str] = []
    #: Each subscription the host reports, once, with its newest usage and the runners spending it, by slug.
    subscription_usage: list[HostSubscriptionUsageView] = []


class HostRegistryListResponse(WireModel):
    """The host registry as operators list it — every added host, oldest first."""

    hosts: list[HostRegistryView] = []


class HostRunnersRequest(BaseModel):
    """Place runners under a host, or return them to standing alone — records who did it."""

    by: str = "operator"
    runner_ids: Annotated[list[str], Field(min_length=1)]


class HostLifecycleRequest(BaseModel):
    """Revoke a host's token — records who did it."""

    by: str = "operator"


class HostRetireRequest(BaseModel):
    """Retire a host and every runner under it — ``force`` releases every chunk they still hold."""

    by: str = "operator"
    force: bool = False


class HostEnrollmentResponse(BaseModel):
    """A rotated bearer token for an existing host id.

    ``token`` is the plaintext, visible only here — only its sha256 hash is kept. The old token stops resolving the
    moment this response lands."""

    host_id: str
    token: str


class HostTokenRevocationResponse(BaseModel):
    """The host after its token was revoked — still added, now unenrolled."""

    host: HostRegistryView


class HostRetireResponse(WireModel):
    """The retired host, plus every chunk the retire released from the runners under it."""

    host: HostRegistryView
    released_chunk_ids: list[str] = []
