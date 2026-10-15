"""The host's registration step — the head of every host tick on a host that holds a host token.

The host reports itself to its hub under its own token: its public URL, the panel's callback URIs, its workspaces
and the subscriptions it declares. The hub's first answer is recorded as the host's identity, once and never edited
(the row first, then the holder: a crash between them leaves the holder to be rebuilt from the row). A later
answer naming another host or tenant is a refusal of the host's credential, not an update.

From an answered report whose identity matches, the step judges each of the host's runners: held under the host,
not held, or standing in another tenant than the host's. It writes that standing into the runner's identity
verdict, which halts a runner the host does not hold through the same gate a token mismatch takes — alone, so the
other runners keep working. Any other outcome — a refused credential, a hub from before hosts, an answer naming
another host — leaves every runner's standing unknown, which never refuses; an unreachable hub leaves the standings
as they stood. The step holds its latest outcome for the host's own reads and for the host's lane, which drains
only while the host is registered with a matching identity."""

from __future__ import annotations

import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass, field
from datetime import datetime
from typing import Protocol

from blizzard.foundation.clock import IClock
from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.foundation.logging import get_logger
from blizzard.foundation.roles import domain_model
from blizzard.runner.hub.client import (
    HostRecord,
    HostRefusalReason,
    HostRefused,
    HostReport,
    HubClientError,
    IHostHubClient,
)
from blizzard.runner.hub.host_identity import (
    HostIdentity,
    HostIdentityAlreadyRecorded,
    HostIdentityHolder,
    IWriteHostIdentityRepository,
)
from blizzard.runner.hub.identity import HostStanding, ICurrentRunnerIdentity, IHostStandingSink

_log = get_logger("blizzard.runner.host")


@domain_model
@dataclass(frozen=True)
class HostRegistrationStatus:
    """The host registration step's latest outcome: its ``state``, when the hub last answered a report with the
    host's identity, the name and runners that answer held, and — short of registered — why."""

    state: HostRegistrationState
    answered_at: datetime | None = None
    host_name: str | None = None
    held_runner_ids: tuple[str, ...] = ()
    detail: str | None = None

    def registered(self) -> bool:
        """Whether the host holds a host token — every state but unregistered, whatever the hub last answered."""
        return self.state is not HostRegistrationState.UNREGISTERED

    def drains(self) -> bool:
        """Whether the host's lane may push: only while the latest report was answered with a matching identity."""
        return self.state is HostRegistrationState.REGISTERED


class ICurrentHostRegistration(Protocol):
    """The host registration step's latest outcome, read by the host's lane, its routes and its panel."""

    def current(self) -> HostRegistrationStatus: ...


class HostRegistrationHolder:
    """The process-lifetime :class:`ICurrentHostRegistration`, written by the host tick and read by request threads."""

    def __init__(self, status: HostRegistrationStatus) -> None:
        self._lock = threading.Lock()
        self._status = status

    @classmethod
    def for_host(cls, *, registered: bool) -> HostRegistrationHolder:
        """A holder for a host that does or does not hold a host token, before its first report."""
        state = HostRegistrationState.PENDING if registered else HostRegistrationState.UNREGISTERED
        return cls(HostRegistrationStatus(state=state))

    def current(self) -> HostRegistrationStatus:
        with self._lock:
            return self._status

    def hold(self, status: HostRegistrationStatus) -> HostRegistrationStatus:
        """Answer ``status`` from now on, returning the one it replaced."""
        with self._lock:
            was, self._status = self._status, status
        return was


def _conforms_current_host_registration(x: HostRegistrationHolder) -> ICurrentHostRegistration:
    return x


class IStandingRunner(Protocol):
    """One of the host's runners, as the registration step judges it: its configured id, the identity its own
    registration recorded, and the verdict its standing under the host folds into."""

    @property
    def runner_id(self) -> str: ...
    @property
    def identity(self) -> ICurrentRunnerIdentity: ...
    @property
    def identity_verdict(self) -> IHostStandingSink: ...


def standing_of(runner: IStandingRunner, record: HostRecord, identity: HostIdentity) -> tuple[HostStanding, str | None]:
    """``runner``'s standing under the host ``record`` answers for, and why it halts the runner when it does."""
    if runner.runner_id not in {held.runner_id for held in record.runners}:
        return HostStanding.NOT_HELD, f"not held under this host ({identity.host_id}) at its hub"
    own = runner.identity.current()
    if own is not None and own.tenant_id is not None and own.tenant_id != identity.tenant_id:
        return (
            HostStanding.TENANT_DIFFERS,
            f"stands in tenant {own.tenant_id}, not the host's tenant {identity.tenant_id}",
        )
    return HostStanding.HELD, None


@dataclass
class HostRegistration:
    """Report the host, record its identity from the first answer, and judge each runner's standing.

    ``client`` is ``None`` on a host with no host token, and the step then does nothing."""

    client: IHostHubClient | None
    report: Callable[[], HostReport]
    identities: IWriteHostIdentityRepository
    identity: HostIdentityHolder
    status: HostRegistrationHolder
    runners: Callable[[], Sequence[IStandingRunner]]
    clock: IClock
    _lock: threading.Lock = field(default_factory=threading.Lock, init=False, repr=False)

    def run(self) -> None:
        if self.client is None:
            return
        # The start-up report and the host tick's never interleave.
        with self._lock:
            try:
                self._run(self.client)
            except Exception:
                # A raise must not reach the host tick's other steps.
                _log.exception("host registration failed — continuing the host tick")

    def _run(self, client: IHostHubClient) -> None:
        try:
            answer = client.report(self.report())
        except HubClientError as exc:
            # An outage: the standings judged from the last answer hold.
            self._hold(
                HostRegistrationStatus(
                    state=HostRegistrationState.UNREACHABLE,
                    answered_at=self.status.current().answered_at,
                    host_name=self.status.current().host_name,
                    held_runner_ids=self.status.current().held_runner_ids,
                    detail=str(exc),
                )
            )
            return
        if isinstance(answer, HostRefused):
            self._unknown_standings()
            state = (
                HostRegistrationState.NO_HOSTS
                if answer.reason is HostRefusalReason.NOT_FOUND
                else HostRegistrationState.REFUSED
            )
            self._hold(HostRegistrationStatus(state=state, detail=f"{answer.status}: {answer.detail}"))
            return
        now = self.clock.now()
        identity, held = self._identity_for(answer, now)
        if identity is None:
            self._unknown_standings()
            self._hold(
                HostRegistrationStatus(
                    state=HostRegistrationState.REFUSED,
                    detail=(
                        f"the hub answered as host {answer.host_id} in tenant {answer.tenant_id}, but this host is "
                        f"recorded as {held.host_id} in {held.tenant_id}"
                        if held is not None
                        else None
                    ),
                )
            )
            return
        self._hold(
            HostRegistrationStatus(
                state=HostRegistrationState.REGISTERED,
                answered_at=now,
                host_name=answer.host_name,
                held_runner_ids=tuple(held_runner.runner_id for held_runner in answer.runners),
            )
        )
        for runner in self.runners():
            standing, reason = standing_of(runner, answer, identity)
            runner.identity_verdict.stand(standing, reason=reason)

    def _identity_for(self, answer: HostRecord, now: datetime) -> tuple[HostIdentity | None, HostIdentity | None]:
        """The host's identity when ``answer`` names it — recording it from a first answer — else ``None``, beside
        the identity the host holds."""
        held = self.identity.current()
        if held is None:
            offered = HostIdentity(host_id=answer.host_id, tenant_id=answer.tenant_id, registered_at=now)
            try:
                # The row first, then the holder: a crash between them leaves the holder rebuilt from the row.
                self.identities.record_host_identity(offered)
                held = offered
            except HostIdentityAlreadyRecorded as exc:
                held = exc.held
            self.identity.hold(held)
        if held.same_host(answer.host_id, answer.tenant_id):
            return held, held
        return None, held

    def _unknown_standings(self) -> None:
        for runner in self.runners():
            runner.identity_verdict.stand(HostStanding.UNKNOWN)

    def _hold(self, status: HostRegistrationStatus) -> None:
        was = self.status.hold(status)
        if was.state is status.state:
            return
        if status.state is HostRegistrationState.REGISTERED:
            _log.info("host registered at its hub", host_name=status.host_name, was=str(was.state))
        else:
            _log.warning("host not registered at its hub", state=str(status.state), detail=status.detail)
