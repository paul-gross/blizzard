"""The one query answering which enabled configured records name a forge (package-private).

:func:`active_referrers` unions every referring kind, as
``src/blizzard/hub/store/internal/secret_referrers.py`` does for a secret; retired derives from each kind's newest
lifecycle fact (``bzh:facts-not-status``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Row, Table
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import RecordKind, RecordRef

# Legacy forge-referrer queries share the latest-retirement selector.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import (
    repositories,
    repository_lifecycle_facts,
    work_source_lifecycle_facts,
    work_sources,
)
from blizzard.hub.store.scoped import statements_of

#: Each referring kind: its record table, its lifecycle-fact table, the id column keying both, and the slug column
#: labelling a record.
_REFERRING: tuple[tuple[RecordKind, Table, Table, str, str], ...] = (
    (RecordKind.REPOSITORY, repositories, repository_lifecycle_facts, "repository_id", "slug"),
    (RecordKind.WORK_SOURCE, work_sources, work_source_lifecycle_facts, "work_source_id", "slug"),
)


def active_referrers(conn: Connection, forge_ids: list[str]) -> dict[str, list[RecordRef]]:
    """For each forge id, the enabled records naming it, ordered by kind then label."""
    found: dict[str, list[RecordRef]] = {forge_id: [] for forge_id in forge_ids}
    if not forge_ids:
        return found
    stmts = statements_of(conn)
    for kind, table, facts, key, label in _REFERRING:
        order = table.c[label]
        rows: list[Row[Any]] = []
        for batch in id_batches(forge_ids):
            rows.extend(
                conn.execute(
                    stmts.select(table, table.c.forge_id, table.c[key], order.label("label"))
                    .where(
                        table.c.forge_id.in_(batch),
                        table.c[key].not_in(newest_retired_select(stmts, facts, facts.c[key])),
                    )
                    .order_by(order)
                ).all()
            )
        rows.sort(key=lambda row: row.label)
        for row in rows:
            found[row.forge_id].append(RecordRef(kind, row[1], row.label))
    return found
