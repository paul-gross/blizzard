"""The one query answering which enabled configured records name a credential (package-private).

:func:`active_referrers` unions every referring kind by its ``credential_id``, as
``src/blizzard/hub/store/internal/forge_referrers.py`` does for a forge; retired derives from each kind's newest
lifecycle fact (``bzh:facts-not-status``)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Row, Table
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.config.changes import RecordKind, RecordRef
from blizzard.hub.domain.config.credential_naming import CredentialId
from blizzard.hub.domain.config.credentials import ICredentialReferences
from blizzard.hub.domain.kernel.transactions import Transaction
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import (
    repositories,
    repository_lifecycle_facts,
    work_source_lifecycle_facts,
    work_sources,
)
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_in

#: Each referring kind: its record table, its lifecycle-fact table, the id column keying both, and the slug column
#: labelling a record.
_REFERRING: tuple[tuple[RecordKind, Table, Table, str, str], ...] = (
    (RecordKind.REPOSITORY, repositories, repository_lifecycle_facts, "repository_id", "slug"),
    (RecordKind.WORK_SOURCE, work_sources, work_source_lifecycle_facts, "work_source_id", "slug"),
)


def active_referrers(conn: Connection, credential_ids: list[str]) -> dict[str, list[RecordRef]]:
    """For each credential id, the enabled records naming it, ordered by kind then label."""
    found: dict[str, list[RecordRef]] = {credential_id: [] for credential_id in credential_ids}
    if not credential_ids:
        return found
    stmts = statements_of(conn)
    for kind, table, facts, key, label in _REFERRING:
        order = table.c[label]
        rows: list[Row[Any]] = []
        for batch in id_batches(credential_ids):
            rows.extend(
                conn.execute(
                    stmts.select(table, table.c.credential_id, table.c[key], order.label("label"))
                    .where(
                        table.c.credential_id.in_(batch),
                        table.c[key].not_in(newest_retired_select(stmts, facts, facts.c[key])),
                    )
                    .order_by(order)
                ).all()
            )
        rows.sort(key=lambda row: row.label)
        for row in rows:
            found[row.credential_id].append(RecordRef(kind, row[1], row.label))
    return found


class CredentialReferrersStore:
    """The credential referrers adapter over the hub store."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def referrers_of(
        self, credential_ids: list[CredentialId], *, transaction: Transaction | None = None
    ) -> dict[CredentialId, list[RecordRef]]:
        with connection_in(self._store, transaction, "referrers_of") as conn:
            return {CredentialId(key): refs for key, refs in active_referrers(conn, list(credential_ids)).items()}


_credential_references_conforms: type[ICredentialReferences] = CredentialReferrersStore
