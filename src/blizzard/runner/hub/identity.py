"""The runner's identity at its hub — the id and name of its latest successful registration, and the
tenant the hub answered with.

The runner learns it from each successful registration's reply and keeps it in its store's single
identity row, so a restart with the hub unreachable keeps it. The tenant is for display: a runner
never changes tenant, so the id alone settles which world the store belongs to. Every step and
consumer reads the process holder (:class:`ICurrentRunnerIdentity`), seeded from the row at boot and
replaced at each successful one."""

from __future__ import annotations

import threading
from collections.abc import Callable
from dataclasses import dataclass, replace
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model
from blizzard.foundation.runner_identity_state import IdentityState


@domain_model
@dataclass(frozen=True)
class RunnerIdentity:
    """Who this runner is at its hub: the hub-minted ``rn_`` id, the name the hub recorded at the
    latest successful registration, when that registration succeeded by the runner's clock, and the
    tenant the hub answered with — ``None`` until a hub that names it answers."""

    runner_id: str
    runner_name: str
    registered_at: datetime
    tenant_id: str | None = None
    tenant_name: str | None = None


def answered_tenant(
    stored: RunnerIdentity | None, tenant_id: str | None, tenant_name: str | None
) -> tuple[str | None, str | None]:
    """The tenant to keep beside the runner's id: the one the hub answered with, or — when the hub
    names none, as one that predates tenants does — the one ``stored`` already holds."""
    if tenant_id is not None:
        return tenant_id, tenant_name
    if stored is not None:
        return stored.tenant_id, stored.tenant_name
    return None, None


class IReadRunnerIdentityRepository(Protocol):
    """Read the runner store's single identity row."""

    def runner_identity(self) -> RunnerIdentity | None:
        """The identity of the latest successful registration, or ``None`` when the runner has
        never registered since its store was created."""
        ...


class IWriteRunnerIdentityRepository(IReadRunnerIdentityRepository, Protocol):
    """Read-write access to the identity row — held only by the registration step."""

    def record_runner_identity(self, identity: RunnerIdentity) -> None:
        """Replace the single identity row with ``identity``, in one transaction."""
        ...


class IWriteRetiredRunnerIdentity(Protocol):
    """Forget a retired runner's identity row — held only by ``runner retire``."""

    def forget_runner_identity(self) -> None:
        """Delete the bound runner's identity row, so the host's store no longer names a runner the hub retired and
        no ``[[runner]]`` configures; its other rows stay, inert. Deleting no row is not an error."""
        ...


class ICurrentRunnerIdentity(Protocol):
    """The process's current runner identity."""

    def current(self) -> RunnerIdentity | None:
        """The identity of the latest successful registration this process knows, or ``None``
        before the runner's first one."""
        ...


class RunnerIdentityHolder:
    """The process-lifetime :class:`ICurrentRunnerIdentity`: seeded with an initial identity, and
    answering each newer one it is handed."""

    def __init__(self, identity: RunnerIdentity | None = None) -> None:
        self._identity = identity

    def current(self) -> RunnerIdentity | None:
        return self._identity

    def hold(self, identity: RunnerIdentity) -> None:
        """Answer ``identity`` from now on — called after the store records it, never before."""
        self._identity = identity


def _conforms_current_runner_identity(x: RunnerIdentityHolder) -> ICurrentRunnerIdentity:
    return x


#: The states in which the runner drives no lease and delivers nothing.
REFUSING_STATES = frozenset(
    {IdentityState.MISMATCHED, IdentityState.REFUSED, IdentityState.NOT_HELD, IdentityState.TENANT_DIFFERS}
)


class HostStanding(StrEnum):
    """Whether the host's hub holds a runner under the host, judged only from an answered report whose identity
    matches the host's. ``UNKNOWN`` never refuses: an unregistered host, a hub that has not answered, or one
    from before hosts."""

    UNKNOWN = "unknown"
    HELD = "held"
    NOT_HELD = "not_held"
    TENANT_DIFFERS = "tenant_differs"


@domain_model
@dataclass(frozen=True)
class IdentityVerdict:
    """The latest identity check: its ``state``, the configured runner, the runner the token
    resolved to, when the hub last answered the check, the remedy that restores the token or the id, and —
    for a refused token or a host standing that halts the runner — why."""

    state: IdentityState
    store_runner_id: str | None
    token_runner_id: str | None
    checked_at: datetime | None
    reset_command: str
    reason: str | None = None

    def refusing(self) -> bool:
        """Whether this verdict halts the runner."""
        return self.state in REFUSING_STATES


class ICurrentIdentityVerdict(Protocol):
    """The process's latest identity verdict."""

    def verdict(self) -> IdentityVerdict: ...

    def matched(self) -> bool:
        """Whether the token is verified to resolve to the configured runner, and nothing refuses it."""
        ...

    def registration_checks(self) -> bool:
        """Whether the hub has no identity read, so the registration reply is the check."""
        ...


class IIdentityOnContact(Protocol):
    """The identity verdict a hub contact outside the loop waits on."""

    def verdict_on_contact(self, ask: Callable[[], str | None]) -> IdentityVerdict:
        """The verdict the contact waits on: the loop's once it has checked; before that, one judged from
        ``ask`` — the runner the hub resolves the token to, or ``None`` when the hub refuses the token or
        has no identity read. Whatever ``ask`` raises when the hub does not answer propagates."""
        ...


class IHostStandingSink(Protocol):
    """Where the host's registration step writes one runner's standing under the host."""

    def stand(self, standing: HostStanding, *, reason: str | None = None) -> None: ...


class IdentityVerdictHolder:
    """The process-lifetime identity verdict, ``UNCHECKED`` from start. Shared by the loop that
    checks it, the host tick that judges its standing, the hub client and proxy it gates, and the status that
    reports it.

    Two inputs held apart: the token's own verdict, which only the identity check and the registration write, and
    the runner's standing under its host, which only the host tick writes. The verdict refuses when either one
    refuses — the token's refusal first — so a check never clears a host refusal and a host answer never clears a
    token's."""

    def __init__(self, reset_command: str, *, state: IdentityState = IdentityState.UNCHECKED) -> None:
        self._lock = threading.Lock()
        self._token = IdentityVerdict(
            state=state, store_runner_id=None, token_runner_id=None, checked_at=None, reset_command=reset_command
        )
        self._standing = HostStanding.UNKNOWN
        self._standing_reason: str | None = None
        self._registration_checks = False

    def verdict(self) -> IdentityVerdict:
        with self._lock:
            return self._composed()

    def _composed(self) -> IdentityVerdict:
        token = self._token
        if token.refusing():
            return token
        if self._standing is HostStanding.NOT_HELD:
            return replace(token, state=IdentityState.NOT_HELD, reason=self._standing_reason)
        if self._standing is HostStanding.TENANT_DIFFERS:
            return replace(token, state=IdentityState.TENANT_DIFFERS, reason=self._standing_reason)
        return token

    def matched(self) -> bool:
        return self.verdict().state is IdentityState.MATCHED

    def token_refusing(self) -> bool:
        """Whether the token's own verdict refuses — what a corrected ``.env`` token can lift."""
        with self._lock:
            return self._token.refusing()

    def standing(self) -> HostStanding:
        with self._lock:
            return self._standing

    def registration_checks(self) -> bool:
        with self._lock:
            return self._registration_checks

    def check_by_registration(self) -> None:
        """The hub has no identity read: let the registration reply decide instead."""
        with self._lock:
            self._registration_checks = True

    def record(
        self, state: IdentityState, *, store_runner_id: str | None, token_runner_id: str, checked_at: datetime
    ) -> IdentityState:
        """Hold the token's verdict from a check the hub answered, returning the composed state it replaced."""
        with self._lock:
            was = self._composed().state
            self._token = IdentityVerdict(
                state=state,
                store_runner_id=store_runner_id,
                token_runner_id=token_runner_id,
                checked_at=checked_at,
                reset_command=self._token.reset_command,
            )
        return was

    def refuse(self, *, store_runner_id: str, reason: str, checked_at: datetime) -> IdentityState:
        """Hold that the hub refused the token for ``reason``, returning the composed state it replaced."""
        with self._lock:
            was = self._composed().state
            self._token = IdentityVerdict(
                state=IdentityState.REFUSED,
                store_runner_id=store_runner_id,
                token_runner_id=None,
                checked_at=checked_at,
                reset_command=self._token.reset_command,
                reason=reason,
            )
        return was

    def stand(self, standing: HostStanding, *, reason: str | None = None) -> None:
        """Hold the runner's standing under its host, as the host tick last judged it."""
        with self._lock:
            self._standing = standing
            self._standing_reason = reason


def _conforms_current_identity_verdict(x: IdentityVerdictHolder) -> ICurrentIdentityVerdict:
    return x


def _conforms_host_standing_sink(x: IdentityVerdictHolder) -> IHostStandingSink:
    return x
