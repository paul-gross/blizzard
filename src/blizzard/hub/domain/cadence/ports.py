"""The cadence repository seams (I-prefix, read/write split — ``bzh:repository-split``)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.cadence.model import Cadence
from blizzard.hub.domain.config.changes import ConfigChange


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

    def create(self, cadence: Cadence, *, change: ConfigChange) -> Cadence:
        """Insert the cadence row and its chores. A slug its project already holds — a create that lost the race
        to it — raises :class:`~blizzard.hub.domain.cadence.model.CadenceSlugTakenError`."""
        ...

    def update(self, cadence: Cadence, *, from_revision: int, change: ConfigChange) -> Cadence:
        """Compare-and-set ``from_revision``, writing the cadence's fields and replacing its chores by id: a kept
        chore's row is rewritten, a new one inserted, and a dropped one's rows deleted;
        :class:`ConfigRevisionConflict` when the stored revision has moved."""
        ...

    def record_lifecycle(
        self, cadence: Cadence, *, retired: bool, from_revision: int, at: datetime, by: str, change: ConfigChange
    ) -> Cadence:
        """Append the retire or enable fact and move the revision to ``cadence.revision``, as one compare-and-set
        on ``from_revision``."""
        ...
