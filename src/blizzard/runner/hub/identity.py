"""The runner's identity at its hub — the id and name of its latest successful registration.

The runner learns it from each successful registration's reply and keeps it in its store's single
identity row, so a restart with the hub unreachable keeps it. Before the first one there is no
identity, and the runner claims nothing and tells no traces. Every step and consumer reads the process
holder (:class:`ICurrentRunnerIdentity`), seeded from the row at boot and replaced at each successful one."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.foundation.roles import domain_model


@domain_model
@dataclass(frozen=True)
class RunnerIdentity:
    """Who this runner is at its hub: the hub-minted ``rn_`` id, the name the hub recorded at the
    latest successful registration, and when that registration succeeded by the runner's clock."""

    runner_id: str
    runner_name: str
    registered_at: datetime


class IReadRunnerIdentityRepository(Protocol):
    """Read the runner store's single identity row (held by read-path edges)."""

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
