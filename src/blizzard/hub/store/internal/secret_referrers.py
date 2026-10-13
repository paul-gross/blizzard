"""The one query answering which active configured records name a secret (package-private).

Both the secret retire refusal (inside its transaction) and the secret view's ``used by``
read through :func:`active_referrers`, so the two never drift. It unions every referring
kind; retired derives from each kind's newest lifecycle fact (``bzh:facts-not-status``)."""

from __future__ import annotations

from sqlalchemy import Table
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import ISecretReferences, RecordKind, RecordRef
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import (
    repositories,
    repository_lifecycle_facts,
    work_source_lifecycle_facts,
    work_sources,
)
from blizzard.hub.store.scoped import ScopedConnections, statements_of

#: Each referring kind: its record table, its lifecycle-fact table, the id column keying both, and the slug column
#: labelling a record.
_REFERRING: tuple[tuple[RecordKind, Table, Table, str, str], ...] = (
    (RecordKind.REPOSITORY, repositories, repository_lifecycle_facts, "repository_id", "slug"),
    (RecordKind.WORK_SOURCE, work_sources, work_source_lifecycle_facts, "work_source_id", "slug"),
)


def active_referrers(conn: Connection, secret_names: list[str]) -> dict[str, list[RecordRef]]:
    """For each secret name, the active records naming it, ordered by kind then key."""
    found: dict[str, list[RecordRef]] = {name: [] for name in secret_names}
    if not secret_names:
        return found
    stmts = statements_of(conn)
    for kind, table, facts, key, label in _REFERRING:
        order = table.c[label]
        rows = []
        for batch in id_batches(secret_names):
            rows.extend(
                conn.execute(
                    stmts.select(table, table.c.secret_name, table.c[key], order.label("label"))
                    .where(
                        table.c.secret_name.in_(batch),
                        table.c[key].not_in(newest_retired_select(stmts, facts, facts.c[key])),
                    )
                    .order_by(order)
                ).all()
            )
        rows.sort(key=lambda row: row.label)
        for row in rows:
            found[row.secret_name].append(RecordRef(kind, row[1], row.label))
    return found


class SecretReferrersStore:
    """The secret view's referrers adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def referrers_of(self, names: list[str]) -> dict[str, list[RecordRef]]:
        with self._store.read("referrers_of") as conn:
            return active_referrers(conn, names)


def _conforms_secret_references(x: SecretReferrersStore) -> ISecretReferences:
    return x
