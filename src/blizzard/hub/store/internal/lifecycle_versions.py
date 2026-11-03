"""Host and runner lifecycle versions, read and checked on the caller's connection — shared by the host and runner
registries, the writers of a runner's placement under a host and of either one's lifecycle (package-private).

A lifecycle version is the id of a key's newest lifecycle fact, ``0`` for a key with none. Every lifecycle transition
appends a fact, so a write decided on one version lands only while no newer fact for its key exists
(``bzh:store-refuses-stale``). The check and the write share the caller's write transaction, which the tenant-row lock
serializes against every other writer of the tenant."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any

from sqlalchemy import Column, Table, func
from sqlalchemy.engine import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.runners.host import HostLifecycleConflict
from blizzard.hub.domain.runners.registration import RunnerLifecycleConflict
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import statements_of


def _versions(conn: Connection, facts: Table, key: Column[Any], keys: Sequence[str]) -> dict[str, int]:
    """Each of ``keys``' newest fact id in ``facts``: one group-by-max per batch."""
    stmts = statements_of(conn)
    found = dict.fromkeys(keys, 0)
    for batch in id_batches(sorted(set(keys))):
        rows = conn.execute(stmts.select(facts, key, func.max(facts.c.id)).where(key.in_(batch)).group_by(key)).all()
        found.update((row[0], int(row[1])) for row in rows)
    return found


def require_host_lifecycle(conn: Connection, host_id: str, expected: int) -> None:
    """:class:`HostLifecycleConflict` when the host no longer stands at lifecycle version ``expected``."""
    facts = s.host_lifecycle_facts
    if _versions(conn, facts, facts.c.host_id, [host_id])[host_id] != expected:
        raise HostLifecycleConflict(host_id, expected=expected)


def require_runner_lifecycles(conn: Connection, expected: Mapping[str, int]) -> None:
    """:class:`RunnerLifecycleConflict` for the first runner, by id, no longer standing at its ``expected`` version."""
    facts = s.runner_lifecycle_facts
    current = _versions(conn, facts, facts.c.runner_id, list(expected))
    for runner_id in sorted(expected):
        if current[runner_id] != expected[runner_id]:
            raise RunnerLifecycleConflict(runner_id, expected=expected[runner_id])
