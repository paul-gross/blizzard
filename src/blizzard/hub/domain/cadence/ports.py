"""The cadence repository seams (I-prefix, read/write split — ``bzh:repository-split``)."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.cadence.errands import ErrandKey, ErrandRun
from blizzard.hub.domain.cadence.model import Cadence
from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.tenancy import TenantId


def errand_lock(key: ErrandKey) -> LockKey:
    """The keyed lock on ``key``'s errand (``bzh:transaction-graduation``) — keyed on its chore and scope ids without
    separator collisions. The cadence mint locks its errand under it, so two mints of one errand decide one after the
    other."""
    return LockKey(LockNamespace.CADENCE_ERRAND, json.dumps([key.chore_id, key.scope_id]))


class IReadCadenceRepository(Protocol):
    """Read-only cadence access, each cadence reconstituted whole with its chores in the order written. A slug
    names a cadence within a project; :meth:`list_all` reads every project of the tenant unless it is given one."""

    def get(self, cadence_id: str) -> Cadence | None: ...

    def get_by_slug(self, project_id: str, slug: str) -> Cadence | None: ...

    def list_all(self, project_id: str | None = None) -> list[Cadence]:
        """Every cadence, retired ones included, newest first — of ``project_id`` only when it is given."""
        ...


class IWriteCadenceRepository(IReadCadenceRepository, Protocol):
    """Adds the cadence writes; each commits the record rows, a compare-and-set on ``revision`` where one is held,
    and its ``change`` in one transaction."""

    def create(self, cadence: Cadence, *, change: ConfigChange, transaction: Transaction | None = None) -> Cadence:
        """Insert the cadence row and its chores — in ``transaction`` when one is given. A slug its project already
        holds — a create that lost the race to it — raises
        :class:`~blizzard.hub.domain.cadence.model.CadenceSlugTakenError`."""
        ...

    def update(
        self, cadence: Cadence, *, from_revision: int, change: ConfigChange, transaction: Transaction | None = None
    ) -> Cadence:
        """Compare-and-set ``from_revision``, writing the cadence's fields and replacing its chores by id: a kept
        chore's row is rewritten, a new one inserted, and a dropped one's rows deleted;
        :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def naming_urgency(self, urgency_id: UrgencyId, *, transaction: Transaction) -> list[Cadence]:
        """Every cadence of the tenant keeping a chore whose override is ``urgency_id``, read in ``transaction``."""
        ...

    def clear_urgency(
        self,
        cadence: Cadence,
        *,
        from_revision: int,
        urgency_id: UrgencyId,
        change: ConfigChange,
        transaction: Transaction,
    ) -> bool:
        """Clear every override of ``urgency_id`` among the cadence's chores and move its revision to
        ``cadence.revision`` with ``change``, only while it stands at ``from_revision``; whether it wrote."""
        ...

    def record_lifecycle(
        self, cadence: Cadence, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Cadence:
        """Append the retire or enable fact and move the revision to ``cadence.revision``, as one compare-and-set
        on ``from_revision``."""
        ...


class IReadErrandRunsRepository(Protocol):
    """Each errand's last run, read in one snapshot (``bzh:pass-reads-one-snapshot``)."""

    def last_runs(
        self, keys: Sequence[ErrandKey], *, transaction: Transaction | None = None
    ) -> dict[ErrandKey, ErrandRun]:
        """The newest work item marked with each of ``keys``, by minted instant then id, with its closure and its
        holder's facts — the live non-ephemeral chunk holding its ref, else the newest terminal one, else none.
        All of it is read as of one moment: in ``transaction`` when one is given, else in one read transaction of
        its own. A key with no marked run has no entry."""
        ...


class IReadCadenceCorpus(Protocol):
    """The cadences read across every open tenant."""

    def tenants_with_live_cadences(self) -> list[TenantId]:
        """Every open tenant holding a cadence that is not retired, in tenant id order."""
        ...
