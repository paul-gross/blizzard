"""Which worker verbs a lease still accepts, by the standing a worker verb resolved it to.

Kept apart from the package root so the concept modules that refuse a verb (asks, the
operator attach) import it without a cycle through the root."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from enum import StrEnum
from types import MappingProxyType

from blizzard.foundation.roles import domain_model
from blizzard.runner.leases.model import Lease

__all__ = ["WORKER_VERBS", "WorkerLease", "WorkerLeaseStanding", "WorkerVerb"]


class WorkerVerb(StrEnum):
    """The token-authorized worker verbs whose acceptance depends on the lease's standing."""

    ASK = "ask"
    ATTACH = "attach"
    GIT_COMMIT = "git-commit"


class WorkerLeaseStanding(StrEnum):
    """Which lease a worker verb resolved to: the active one, or the closed reference lease
    an open takeover names."""

    ACTIVE = "active"
    TAKEOVER_REFERENCE = "takeover-reference"


#: The worker verbs each standing accepts; a closed reference lease refuses an ask and an attachment.
WORKER_VERBS: Mapping[WorkerLeaseStanding, frozenset[WorkerVerb]] = MappingProxyType(
    {
        WorkerLeaseStanding.ACTIVE: frozenset(WorkerVerb),
        WorkerLeaseStanding.TAKEOVER_REFERENCE: frozenset({WorkerVerb.GIT_COMMIT}),
    }
)


@domain_model
@dataclass(frozen=True)
class WorkerLease:
    """The lease a worker verb acts against, and whether it is still the active lease.

    ``active`` is ``False`` only for the closed reference lease an open takeover names;
    :data:`WORKER_VERBS` says which verbs it still accepts."""

    lease: Lease
    active: bool

    @property
    def standing(self) -> WorkerLeaseStanding:
        return self._standing()

    def _standing(self) -> WorkerLeaseStanding:
        return WorkerLeaseStanding.ACTIVE if self.active else WorkerLeaseStanding.TAKEOVER_REFERENCE

    def accepts(self, verb: WorkerVerb) -> bool:
        """Whether ``verb`` is legal against this lease's standing."""
        return verb in WORKER_VERBS[self.standing]
