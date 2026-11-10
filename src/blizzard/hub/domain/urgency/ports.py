"""The urgency repository seams (I-prefix, read/write split — ``bzh:repository-split``) and the keyed locks a unit
setting or retiring an urgency declares (``bzh:store-exclusive-write``).

Every read and write takes an optional ``transaction`` so a unit re-reads under its locks and writes in the one
transaction it opened (``bzh:transaction-participation``)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.config.changes import ConfigChange
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.domain.urgency.mark import Icon
from blizzard.hub.domain.urgency.model import MarkWrite, Urgency, UrgencyId
from blizzard.hub.domain.urgency.order import PositionFact, UrgencyOrder
from blizzard.hub.domain.urgency.settings import TenantSettings

#: One urgency's keyed lock, keyed by its id: every write setting something to the urgency, and its retire, take it.
URGENCY_LOCK_NAMESPACE = "urgency"
#: The tenant's urgency set's keyed lock, on one fixed key: a create and each reorder take it.
URGENCY_ORDER_LOCK_NAMESPACE = "urgency_order"
#: The tenant's settings' keyed lock, on one fixed key: every change of the default, every retire, and every mint
#: take it, so the default holds still under a mint.
TENANT_SETTINGS_LOCK_NAMESPACE = "tenant_settings"

_SINGLETON_KEY = "tenant"


def urgency_lock(urgency_id: UrgencyId) -> tuple[str, str]:
    return URGENCY_LOCK_NAMESPACE, urgency_id


def urgency_order_lock() -> tuple[str, str]:
    return URGENCY_ORDER_LOCK_NAMESPACE, _SINGLETON_KEY


def tenant_settings_lock() -> tuple[str, str]:
    return TENANT_SETTINGS_LOCK_NAMESPACE, _SINGLETON_KEY


class IReadUrgencyRepository(Protocol):
    """Read-only urgency access, within one tenant; each urgency read whole with its retired brake."""

    def get(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> Urgency | None: ...

    def get_by_slug(self, slug: str, *, transaction: Transaction | None = None) -> Urgency | None: ...

    def list_all(self, *, transaction: Transaction | None = None) -> list[Urgency]:
        """Every urgency of the tenant, retired ones included, by slug."""
        ...

    def icon(self, urgency_id: UrgencyId) -> Icon | None:
        """The urgency's icon bytes; ``None`` when it carries none or does not exist."""
        ...

    def icons(self) -> dict[UrgencyId, Icon]:
        """The icon bytes of every urgency of the tenant carrying one, keyed by id, in one read."""
        ...


class IWriteUrgencyRepository(IReadUrgencyRepository, Protocol):
    """Adds the urgency writes; each commits the record, a compare-and-set on ``revision`` where one is held, and its
    ``change`` in one transaction."""

    def create(
        self,
        urgency: Urgency,
        *,
        mark: MarkWrite,
        position: float,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> Urgency:
        """Insert the urgency with ``mark``'s icon bytes and its first position fact at ``position``. Outside a unit, a
        slug the tenant already holds — a create that lost the race to it — raises
        :class:`~blizzard.hub.domain.urgency.model.UrgencySlugTaken`; inside one, the caller holds the set's lock and
        has re-read the slug free under it, so no create races it there."""
        ...

    def update(
        self,
        urgency: Urgency,
        *,
        mark: MarkWrite,
        from_revision: int,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> Urgency:
        """Compare-and-set ``from_revision``, writing the urgency's fields and ``mark``'s icon bytes;
        :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(
        self,
        urgency: Urgency,
        *,
        retired: bool,
        from_revision: int,
        at: datetime,
        by: str,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> Urgency:
        """Append the retire or enable fact and move the revision to ``urgency.revision``, as one compare-and-set on
        ``from_revision``."""
        ...


class IReadUrgencyOrder(Protocol):
    """The tenant's urgencies at their places."""

    def order(self, *, transaction: Transaction | None = None) -> UrgencyOrder:
        """Every urgency at its newest position, retired ones included, in order, with the order's sequence read
        before the positions."""
        ...


class IWriteUrgencyOrder(IReadUrgencyOrder, Protocol):
    """Adds the position write."""

    def record_positions(
        self,
        facts: Sequence[PositionFact],
        *,
        from_seq: int,
        at: datetime,
        by: str,
        change: ConfigChange | None,
        transaction: Transaction | None = None,
    ) -> int:
        """Append one position fact per entry of ``facts``, in order, and ``change`` with its revision set to the
        order's sequence once they land, only while the sequence stands at ``from_seq``;
        :class:`~blizzard.hub.domain.urgency.order.UrgencyOrderConflict` otherwise, appending nothing. The sequence
        after the write — ``from_seq`` when ``facts`` is empty, which writes nothing."""
        ...


class IReadTenantSettings(Protocol):
    """The tenant's settings."""

    def get(self, *, transaction: Transaction | None = None) -> TenantSettings | None:
        """The tenant's settings — ``None`` only for a tenant whose creation has not yet seeded them."""
        ...


class IWriteTenantSettings(IReadTenantSettings, Protocol):
    """Adds the settings writes; each commits its ``change`` in the same transaction."""

    def create(
        self, settings: TenantSettings, *, change: ConfigChange, transaction: Transaction | None = None
    ) -> TenantSettings:
        """Insert the tenant's settings — a tenant's creation seeds them once."""
        ...

    def update(
        self,
        settings: TenantSettings,
        *,
        from_revision: int,
        change: ConfigChange,
        transaction: Transaction | None = None,
    ) -> TenantSettings:
        """Compare-and-set ``from_revision``; :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...


class IReadUrgencyChunks(Protocol):
    """The open chunks carrying an urgency — the non-terminal chunks, by their derived status, never by a prefilter
    alone."""

    def open_counts(self, urgency_ids: Sequence[UrgencyId]) -> dict[UrgencyId, int]:
        """How many open chunks carry each of ``urgency_ids``, in one grouped read; ``0`` for one none carries."""
        ...

    def open_chunks(self, urgency_id: UrgencyId, *, transaction: Transaction | None = None) -> list[str]:
        """The ids of the open chunks carrying ``urgency_id``, by id."""
        ...


class IWriteUrgencyChunks(IReadUrgencyChunks, Protocol):
    """Adds the move a retire makes."""

    def move(
        self,
        chunk_ids: Sequence[str],
        *,
        from_id: UrgencyId,
        to: UrgencyId,
        transaction: Transaction | None = None,
    ) -> list[str]:
        """Set each chunk of ``chunk_ids`` still carrying ``from_id`` and still open to carry ``to``, in one bulk
        write, its rank untouched; the ids of the chunks it moved, by id. A chunk that finished since it was read
        keeps its urgency."""
        ...
