"""The one query answering which active configured records name a secret (package-private).

Both the secret retire refusal (inside its transaction) and the secret view's ``used by``
read through :func:`active_referrers`, so the two never drift. It unions every referring
kind; retired derives from each kind's newest lifecycle fact (``bzh:facts-not-status``)."""

from __future__ import annotations

from sqlalchemy import Table, select
from sqlalchemy.engine import Connection

from blizzard.hub.domain.config.changes import ISecretReferences, RecordKind, RecordRef
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.newest_fact import newest_retired_select
from blizzard.hub.store.schema import (
    repositories,
    repository_lifecycle_facts,
    work_source_lifecycle_facts,
    work_sources,
)

#: Each referring kind: its record table and its lifecycle-fact table.
_REFERRING: tuple[tuple[RecordKind, Table, Table], ...] = (
    (RecordKind.REPOSITORY, repositories, repository_lifecycle_facts),
    (RecordKind.WORK_SOURCE, work_sources, work_source_lifecycle_facts),
)


def active_referrers(conn: Connection, secret_names: list[str]) -> dict[str, list[RecordRef]]:
    """For each secret name, the active records naming it, ordered by kind then key."""
    found: dict[str, list[RecordRef]] = {name: [] for name in secret_names}
    if not secret_names:
        return found
    for kind, table, facts in _REFERRING:
        rows = conn.execute(
            select(table.c.secret_name, table.c.name)
            .where(
                table.c.secret_name.in_(secret_names), table.c.name.not_in(newest_retired_select(facts, facts.c.name))
            )
            .order_by(table.c.name)
        ).all()
        for row in rows:
            found[row.secret_name].append(RecordRef(kind, row.name))
    return found


class SecretReferrersStore:
    """The secret view's referrers adapter over the hub store."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def referrers_of(self, names: list[str]) -> dict[str, list[RecordRef]]:
        with self._store.read("referrers_of") as conn:
            return active_referrers(conn, names)


def _conforms_secret_references(x: SecretReferrersStore) -> ISecretReferences:
    return x
