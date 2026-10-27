"""Host domain — the tenant-scoped record above runners, its credential, and its report.

A host's ``host_id`` is the hub-minted ``hs_`` id, its one identity. Derived rather than stored: **liveness**
(``last_seen_at`` against :data:`~blizzard.hub.domain.runners.registration.STALE_AFTER`, at read time), **never
connected** (no ``registered_at``) and **retired** (the newest appended lifecycle fact). ``token_hash`` is the one
mutable exception; a revoked hash is kept as a fact."""

from __future__ import annotations

from collections.abc import Collection, Iterable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_connection import RunnerConnection
from blizzard.foundation.store.utc import as_utc
from blizzard.hub.domain.runners.registration import (
    STALE_AFTER,
    DeclaredSubscription,
    RunnerRegistration,
    RunnerRetired,
    SubscriptionUsageMiss,
    SubscriptionUsageSample,
    UnregisteredRedirect,
    declared_name,
)
from blizzard.hub.domain.runners.route import Route


class HostState(StrEnum):
    """A host's lifecycle state, derived from its row: whether a token is enrolled, and retired first."""

    UNENROLLED = "unenrolled"
    ENROLLED = "enrolled"
    #: Retired implies unenrolled — retirement revokes the token in the same write.
    RETIRED = "retired"


class HostVerb(StrEnum):
    """Every verb that acts on a host."""

    #: The host's own contact — every route a host calls for itself.
    CONTACT = "contact"
    ADD_RUNNER = "add-runner"
    ADOPT = "adopt"
    RELEASE = "release"
    ENROLL = "enroll"
    REVOKE_TOKEN = "revoke-token"
    RETIRE = "retire"


_ACTIVE_VERBS = frozenset(
    {HostVerb.CONTACT, HostVerb.ADD_RUNNER, HostVerb.ADOPT, HostVerb.RELEASE, HostVerb.ENROLL, HostVerb.RETIRE}
)

#: Which verbs are legal from which state; :class:`Host` refuses what the state alone cannot.
HOST_VERBS: Mapping[HostState, frozenset[HostVerb]] = {
    HostState.UNENROLLED: _ACTIVE_VERBS,
    HostState.ENROLLED: _ACTIVE_VERBS | {HostVerb.REVOKE_TOKEN},
    HostState.RETIRED: frozenset({HostVerb.RETIRE, HostVerb.RELEASE, HostVerb.REVOKE_TOKEN}),
}


@domain_model
@dataclass(frozen=True)
class HostWorkspace:
    """One workspace a host reported: its id and how many environments it holds."""

    workspace_id: str
    env_capacity: int | None = None


@domain_model
@dataclass(frozen=True)
class HostLifecycleFact:
    """The retire fact to append."""

    host_id: str
    retired: bool
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class HostTokenRevocation:
    """Revoke the host's current token: record its hash as revoked and null it, in one write."""

    host_id: str
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class HostTokenRotation:
    """Enroll a fresh token hash. Any hash it replaces is recorded as revoked in the same write."""

    host_id: str
    token_hash: str
    at: datetime
    by: str


@domain_model
@dataclass(frozen=True)
class HostAddition:
    """Add a host: a never-connected record under ``host_id`` and its ``name``, holding ``token_hash``, recorded as
    added ``at`` by ``by``. ``adopt`` are the runners placed under it in the same write."""

    host_id: str
    name: str
    token_hash: str
    at: datetime
    by: str
    adopt: tuple[str, ...] = ()


@domain_model
@dataclass(frozen=True)
class HostReport:
    """What a host reports about itself on each tick. A blank ``name`` keeps the held one."""

    name: str | None
    url: str | None
    redirect_uris: tuple[str, ...]
    workspaces: tuple[HostWorkspace, ...]
    subscriptions: tuple[DeclaredSubscription, ...]


@domain_model
@dataclass(frozen=True)
class ReportDecision:
    """What recording a :class:`HostReport` does: the ``name`` to record (``None`` keeps the held one) and whether
    it ``changed`` what the hub holds — or found the host offline, or was the host's first."""

    name: str | None
    changed: bool


@domain_model
@dataclass(frozen=True)
class Host:
    """A host record with its derived retirement."""

    host_id: str
    name: str
    added_at: datetime
    added_by: str | None = None
    registered_at: datetime | None = None
    last_seen_at: datetime | None = None
    url: str | None = None
    redirect_uris: tuple[str, ...] = ()
    workspaces: tuple[HostWorkspace, ...] = ()
    subscriptions: tuple[DeclaredSubscription, ...] = ()
    #: The enrolled bearer token's sha256 hex digest — never the plaintext. ``None`` for an unenrolled host.
    token_hash: str | None = None
    #: Derived from the newest lifecycle fact; ``retired_at``/``retired_by`` populate only while retired.
    retired: bool = False
    retired_at: datetime | None = None
    retired_by: str | None = None

    def never_connected(self) -> bool:
        """Whether the host was added but has never reported."""
        return self.registered_at is None

    def state(self) -> HostState:
        """The lifecycle state this row derives: retired first, then whether a token is enrolled."""
        if self.retired:
            return HostState.RETIRED
        return HostState.UNENROLLED if self.token_hash is None else HostState.ENROLLED

    def permits(self, verb: HostVerb) -> bool:
        """Whether ``verb`` is legal from this host's state (:data:`HOST_VERBS`)."""
        return verb in HOST_VERBS[self.state()]

    def online(self, *, now: datetime, threshold: timedelta = STALE_AFTER) -> bool:
        """Online iff the host has reported and was seen within ``threshold`` of ``now``."""
        seen = self.last_seen_at
        if self.never_connected() or seen is None:
            return False
        return (as_utc(now) - as_utc(seen)) <= threshold

    def connection(self, *, now: datetime, threshold: timedelta = STALE_AFTER) -> RunnerConnection:
        """The host's connection condition: never connected before its first report, else online or offline."""
        if self.never_connected():
            return RunnerConnection.NEVER_CONNECTED
        return RunnerConnection.ONLINE if self.online(now=now, threshold=threshold) else RunnerConnection.OFFLINE

    def refuse_if_retired(self, *, action: str) -> None:
        """Raise :class:`HostRetired` when this host is retired — the one guard every operation a retired host
        must not perform enforces, whatever token admitted the caller."""
        if not self.permits(HostVerb.CONTACT):
            raise HostRetired(self.host_id, action=action)

    def report(
        self,
        reported: HostReport,
        *,
        other_live_names: Collection[str],
        now: datetime,
        threshold: timedelta = STALE_AFTER,
    ) -> ReportDecision:
        """The decision for ``reported``. A report is never refused over its content, so a name another live host
        already holds is not recorded: the held name stays. A retired host raises :class:`HostRetired`."""
        self.refuse_if_retired(action="report")
        name = declared_name(reported.name)
        if name is not None and name != self.name and name in other_live_names:
            name = None
        changed = (
            self.never_connected()
            or not self.online(now=now, threshold=threshold)
            or (name is not None and name != self.name)
            or reported.url != self.url
            or reported.redirect_uris != self.redirect_uris
            or reported.workspaces != self.workspaces
            or reported.subscriptions != self.subscriptions
        )
        return ReportDecision(name=name, changed=changed)

    def refuse_federation(self, redirect_uri: str) -> None:
        """Refuse an IdP bounce to ``redirect_uri`` for this host: :class:`UnregisteredRedirect` unless the URI is
        one it reported (the open-redirect guard), then :class:`HostRetired`. In that order, so only a caller
        already holding a reported redirect URI can tell a retired host apart."""
        if not self.is_federation_target(redirect_uri):
            raise UnregisteredRedirect(self.host_id)
        self.refuse_if_retired(action="federation")

    def is_federation_target(self, redirect_uri: str) -> bool:
        """Whether the IdP may bounce to ``redirect_uri`` for this host — the URI must be one it reported."""
        return redirect_uri in self.redirect_uris

    def enroll(self, token_hash: str, *, by: str, at: datetime) -> HostTokenRotation:
        """Mint (or rotate to) ``token_hash``. A retired host raises :class:`HostRetired`: retirement is final."""
        if not self.permits(HostVerb.ENROLL):
            raise HostRetired(self.host_id, action="enrollment")
        return HostTokenRotation(host_id=self.host_id, token_hash=token_hash, at=at, by=by)

    def revoke_token(self, *, by: str, at: datetime) -> HostTokenRevocation:
        """Revoke the held token. Raises :class:`HostNotEnrolled` when it holds none."""
        if not self.permits(HostVerb.REVOKE_TOKEN) or self.token_hash is None:
            raise HostNotEnrolled(self.host_id)
        return HostTokenRevocation(host_id=self.host_id, at=at, by=by)

    def adopt(self, runner: RunnerRegistration) -> None:
        """Refuse placing ``runner`` under this host: :class:`HostRetired` for a retired host,
        :class:`~blizzard.hub.domain.runners.registration.RunnerRetired` for a retired runner, and
        :class:`RunnerAlreadyHosted` for one under any host, this one included."""
        if not self.permits(HostVerb.ADOPT):
            raise HostRetired(self.host_id, action="adoption")
        if runner.retired:
            raise RunnerRetired(runner.runner_id, action="adoption")
        if runner.host_id is not None:
            raise RunnerAlreadyHosted(runner.runner_id, host_id=runner.host_id)

    def release(self, runner: RunnerRegistration) -> None:
        """Refuse returning ``runner`` to standing alone unless it stands under this host."""
        if runner.host_id != self.host_id:
            raise RunnerNotUnderHost(runner.runner_id, host_id=self.host_id)

    def refuse_adding_runner(self) -> None:
        """Raise :class:`HostRetired` when this host may add no runner."""
        if not self.permits(HostVerb.ADD_RUNNER):
            raise HostRetired(self.host_id, action="adding a runner")

    def retire(
        self, holdings: Mapping[str, Sequence[Route]], *, force: bool, by: str, at: datetime
    ) -> HostLifecycleFact | None:
        """The retire fact to append, or ``None`` on a re-run over an already-retired host, which writes no second
        fact. ``holdings`` are the live routes, per runner under the host, on chunks that still hold a claim; a first
        retire without ``force`` that finds any raises :class:`HostHoldsRoutes`."""
        if self.state() is HostState.RETIRED:
            return None
        held = {runner_id: list(routes) for runner_id, routes in holdings.items() if routes}
        if held and not force:
            raise HostHoldsRoutes(self.host_id, held)
        return HostLifecycleFact(host_id=self.host_id, retired=True, at=at, by=by)


@domain_model
@dataclass(frozen=True)
class HostUsage:
    """A host's newest reported sample and miss per subscription slug, raw — staleness is applied at derive time."""

    samples: tuple[SubscriptionUsageSample, ...] = ()
    misses: tuple[SubscriptionUsageMiss, ...] = ()


class IReadHostRegistry(Protocol):
    """Read-only host registry access."""

    def get_host(self, host_id: str) -> Host | None: ...

    def get_hosts(self, host_ids: Iterable[str]) -> dict[str, Host]:
        """Each named host, retired ones included, keyed by its id; an id with no host is absent. One read per id
        batch, none for no ids."""
        ...

    def list_hosts(self, *, include_retired: bool = False) -> list[Host]:
        """Every host, oldest added first — retired hosts only when ``include_retired``."""
        ...

    def live_names(self, *, excluding: str | None = None) -> frozenset[str]:
        """The names of the tenant's hosts that are not retired, ``excluding`` one host's own."""
        ...

    def high_water(self, host_id: str) -> int:
        """The greatest per-host fact seq already applied; ``0`` for a host that has pushed none."""
        ...

    def usage_of(self, host_ids: Sequence[str]) -> dict[str, HostUsage]:
        """Each listed host's newest sample and miss per slug, one entry per id, in one read per id batch."""
        ...


class IWriteHostRegistry(IReadHostRegistry, Protocol):
    """Adds the host registry writes."""

    def add(self, addition: HostAddition) -> None:
        """Insert ``addition`` as a never-connected host and place its ``adopt`` runners under it, in one
        transaction. Raises :class:`HostNameTaken` when a live host already holds the name, and
        :class:`RunnerAlreadyHosted` when an adopted runner came to stand under a host first."""
        ...

    def record_report(self, host_id: str, report: HostReport, *, name: str | None, at: datetime) -> bool:
        """Record a report of the added host ``host_id``, refreshing ``last_seen_at``; returns True on its first one,
        which also stamps ``registered_at``. ``name`` replaces the held name unless ``None``; every other field is
        written verbatim. A ``host_id`` with no row raises :class:`LookupError`; a ``name`` a live host came to hold
        first raises :class:`HostNameTaken`, with nothing written."""
        ...

    def adopt(self, host_id: str, runner_ids: Sequence[str]) -> None:
        """Place the runners under ``host_id``, in one transaction. Raises :class:`RunnerAlreadyHosted` when one
        came to stand under a host first."""
        ...

    def set_high_water(self, host_id: str, *, seq: int, at: datetime) -> None:
        """Record ``seq`` as the greatest per-host fact seq applied."""
        ...

    def record_external_usage(
        self, host_id: str, *, slug: str, name: str, sampled_at: datetime, windows_json: str, at: datetime
    ) -> None:
        """Upsert the host's newest sample for ``slug``; no row for ``host_id`` is required."""
        ...

    def record_external_usage_miss(
        self, host_id: str, *, slug: str, name: str, missed_at: datetime, reason: str, at: datetime
    ) -> None:
        """Upsert the host's newest miss for ``slug``, in its own table; no row for ``host_id`` is required."""
        ...

    def release(self, host_id: str, runner_ids: Sequence[str]) -> None:
        """Return the runners under ``host_id`` to standing alone, keeping their ids and tokens."""
        ...

    def record_retirement(
        self, host_id: str, *, fact: HostLifecycleFact | None, at: datetime, by: str
    ) -> tuple[int | None, int | None]:
        """Append ``fact`` (when given) and revoke the host's token, in one transaction. Returns the lifecycle fact
        id and the revocation id, each ``None`` when it wrote none."""
        ...

    def revoke_token(self, host_id: str, *, at: datetime, by: str) -> int | None:
        """Record the host's current token hash as revoked and null it, in one transaction. Returns the
        ``host_token_revocations.id``, or ``None`` when no token was enrolled."""
        ...

    def rotate_token(self, rotation: HostTokenRotation) -> int | None:
        """Set the host's token hash, recording any hash it replaces as revoked in the same transaction. Returns the
        revocation id, or ``None`` on a first enrollment."""
        ...


class HostRetired(Exception):
    """The host is retired — refused whatever token admitted the caller, keyed on its id."""

    def __init__(self, host_id: str, *, action: str) -> None:
        super().__init__(f"host {host_id} is retired — {action} refused")
        self.host_id = host_id


class HostNameTaken(Exception):
    """A live host of the tenant already holds the name."""

    def __init__(self, name: str) -> None:
        super().__init__(f"a live host is already named {name!r}")
        self.name = name


class HostNotEnrolled(Exception):
    """A token revocation targeted a host with no enrolled token."""

    def __init__(self, host_id: str) -> None:
        super().__init__(f"host {host_id} has no enrolled token to revoke")
        self.host_id = host_id


class UnknownRunner(LookupError):
    """A host verb named a runner the tenant does not hold — the same answer for one that does not exist and for
    one another tenant holds, so a tenant learns of no row it cannot see."""

    def __init__(self, runner_id: str) -> None:
        super().__init__(f"unknown runner {runner_id}")
        self.runner_id = runner_id


class RunnerAlreadyHosted(Exception):
    """An adoption named a runner that already stands under a host."""

    def __init__(self, runner_id: str, *, host_id: str | None = None) -> None:
        under = "" if host_id is None else f" {host_id}"
        super().__init__(f"runner {runner_id} already stands under host{under}")
        self.runner_id = runner_id


class RunnerNotUnderHost(Exception):
    """A host verb named a runner that does not stand under that host."""

    def __init__(self, runner_id: str, *, host_id: str) -> None:
        super().__init__(f"runner {runner_id} does not stand under host {host_id}")
        self.runner_id = runner_id
        self.host_id = host_id


class HostHoldsRoutes(Exception):
    """A retire without ``force`` found runners under the host holding live routes — each runner's held chunks."""

    def __init__(self, host_id: str, holdings: Mapping[str, list[Route]]) -> None:
        held = "; ".join(
            f"runner {runner_id}: " + ", ".join(route.chunk_id for route in routes)
            for runner_id, routes in holdings.items()
        )
        super().__init__(f"host {host_id} has runners holding chunks — {held} — retire with --force to release")
        self.host_id = host_id
        self.holdings = dict(holdings)
