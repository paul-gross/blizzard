"""The runner's identity at its hub — the id and name of its latest successful registration, and the
tenant the hub answered with.

The runner learns it from each successful registration's reply and keeps it in its store's single
identity row, so a restart with the hub unreachable keeps it. The tenant is for display: a runner
never changes tenant, so the id alone settles which world the store belongs to. Before the first
registration there is no identity, and the runner claims nothing and tells no traces. Every step and
consumer reads the process holder (:class:`ICurrentRunnerIdentity`), seeded from the row at boot and
replaced at each successful one."""

from __future__ import annotations

import threading
from dataclasses import dataclass
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from blizzard.foundation.roles import domain_model


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


class IdentityState(StrEnum):
    """Whether this process has verified that its token resolves to the runner its store belongs to."""

    #: Not yet verified since the process started: every hub call answers as during an outage.
    UNCHECKED = "unchecked"
    #: The token resolves to the store's runner, or the store named none and adopted it.
    MATCHED = "matched"
    #: The token resolves to a different runner: the runner refuses to work until it matches again.
    MISMATCHED = "mismatched"


@domain_model
@dataclass(frozen=True)
class IdentityVerdict:
    """The latest identity check: its ``state``, the runner the store belongs to, the runner the token
    resolved to, when the hub last answered the check, and the command that clears the store."""

    state: IdentityState
    store_runner_id: str | None
    token_runner_id: str | None
    checked_at: datetime | None
    reset_command: str


class ICurrentIdentityVerdict(Protocol):
    """The process's latest identity verdict."""

    def verdict(self) -> IdentityVerdict: ...

    def matched(self) -> bool:
        """Whether the token is verified to resolve to the store's runner."""
        ...

    def registration_checks(self) -> bool:
        """Whether the hub has no identity read, so the registration reply is the check."""
        ...


class IdentityVerdictHolder:
    """The process-lifetime identity verdict, ``UNCHECKED`` from start. Shared by the loop that
    checks it, the hub client and proxy it gates, and the status that reports it."""

    def __init__(self, reset_command: str, *, state: IdentityState = IdentityState.UNCHECKED) -> None:
        self._lock = threading.Lock()
        self._verdict = IdentityVerdict(
            state=state, store_runner_id=None, token_runner_id=None, checked_at=None, reset_command=reset_command
        )
        self._registration_checks = False

    def verdict(self) -> IdentityVerdict:
        with self._lock:
            return self._verdict

    def matched(self) -> bool:
        return self.verdict().state is IdentityState.MATCHED

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
        """Hold the verdict of a check the hub answered, returning the state it replaced."""
        with self._lock:
            was = self._verdict.state
            self._verdict = IdentityVerdict(
                state=state,
                store_runner_id=store_runner_id,
                token_runner_id=token_runner_id,
                checked_at=checked_at,
                reset_command=self._verdict.reset_command,
            )
        return was


def _conforms_current_identity_verdict(x: IdentityVerdictHolder) -> ICurrentIdentityVerdict:
    return x
