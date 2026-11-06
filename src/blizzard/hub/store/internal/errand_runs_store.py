"""SQLAlchemy adapter for the cadence errand-runs read seam (package-private).

All ``sqlalchemy`` usage is confined here (``bzh:dependency-inversion``). An errand's runs are the work items whose
``work_item_runs`` mark names its chore and scope; the newest of them is kept in SQL by the anti-join over
``(work_items.created_at, work_item_id)`` (``bzh:newest-per-key-read``), since a work item is a mutable entity with no
append order of its own. The holders and their status facts are read in bulk beside them, through the store's shared
helpers rather than a sibling adapter (``bzh:sibling-adapter-independence``), and every statement runs on one
connection, so the whole read sees the store as of one moment (``bzh:pass-reads-one-snapshot``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Generator, Sequence
from contextlib import contextmanager
from dataclasses import replace

from sqlalchemy import Connection

from blizzard.foundation.store.batching import id_batches
from blizzard.foundation.work_items import WorkItemClosure
from blizzard.hub.domain.cadence.errands import ErrandKey, ErrandRun, Holder, LastRun, holder_of
from blizzard.hub.domain.cadence.ports import IReadErrandRunsRepository
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.domain.kernel.transactions import Transaction

# Loads the holders' status facts on this read's own connection.
# ast-grep-ignore: bzh:hub-store-adapter-imports
from blizzard.hub.store.internal.chunk_facts_store import status_facts_on
from blizzard.hub.store.internal.newest_fact import newer_row_exists
from blizzard.hub.store.schema import chunk_work_refs, work_item_runs, work_items
from blizzard.hub.store.scoped import ScopedConnections, statements_of
from blizzard.hub.store.transactions import connection_for


class ErrandRunsStore:
    """Read-only errand-runs adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def last_runs(
        self, keys: Sequence[ErrandKey], *, transaction: Transaction | None = None
    ) -> dict[ErrandKey, ErrandRun]:
        if not keys:
            return {}
        with self._snapshot(transaction) as conn:
            return _last_runs(conn, set(keys))

    @contextmanager
    def _snapshot(self, transaction: Transaction | None) -> Generator[Connection, None, None]:
        if transaction is not None:
            yield connection_for(self._store, transaction)
            return
        with (
            self._store.read_transaction("errand_runs") as borrowed,
            borrowed.scoped(self._store.scope).read("errand_runs") as conn,
        ):
            yield conn


def _last_runs(conn: Connection, keys: set[ErrandKey]) -> dict[ErrandKey, ErrandRun]:
    stmts = statements_of(conn)
    rival_runs = work_item_runs.alias("rival_runs")
    rival_items = work_items.alias("rival_items")
    beaten = newer_row_exists(
        stmts,
        stmts.scope_join(rival_runs, rival_items, rival_runs.c.work_item_id == rival_items.c.work_item_id),
        partition=[
            (work_item_runs.c.chore_id, rival_runs.c.chore_id),
            (work_item_runs.c.scope_id, rival_runs.c.scope_id),
        ],
        order=[
            (work_items.c.created_at, rival_items.c.created_at),
            (work_items.c.work_item_id, rival_items.c.work_item_id),
        ],
    )
    newest: dict[ErrandKey, tuple[str, str, LastRun]] = {}
    for batch in id_batches(sorted({key.chore_id for key in keys})):
        rows = conn.execute(
            stmts.select(
                stmts.scope_join(
                    work_item_runs, work_items, work_item_runs.c.work_item_id == work_items.c.work_item_id
                ),
                work_item_runs.c.chore_id,
                work_item_runs.c.scope_id,
                work_items.c.work_item_id,
                work_items.c.ref,
                work_items.c.created_at,
                work_items.c.closure,
                work_items.c.closed_at,
            ).where(work_item_runs.c.chore_id.in_(batch), ~beaten)
        ).all()
        for row in rows:
            key = ErrandKey(row.chore_id, row.scope_id)
            if key in keys:
                closure = None if row.closure is None else WorkItemClosure(row.closure)
                run = LastRun(minted_at=row.created_at, closure=closure, closed_at=row.closed_at)
                newest[key] = (row.work_item_id, row.ref, run)
    held = _holders(conn, sorted({ref for _, ref, _ in newest.values()}))
    return {
        key: ErrandRun(work_item_id, replace(run, holder=held.get(ref)))
        for key, (work_item_id, ref, run) in newest.items()
    }


def _holders(conn: Connection, refs: Sequence[str]) -> dict[str, Holder]:
    """Each hub ref's holder among the non-ephemeral chunks holding it; a ref none holds has no entry."""
    stmts = statements_of(conn)
    chunks_by_ref: dict[str, set[str]] = defaultdict(set)
    for batch in id_batches(refs):
        rows = conn.execute(
            stmts.select(chunk_work_refs, chunk_work_refs.c.ref, chunk_work_refs.c.chunk_id).where(
                chunk_work_refs.c.source == RESERVED_HUB_SOURCE_NAME, chunk_work_refs.c.ref.in_(batch)
            )
        ).all()
        for row in rows:
            chunks_by_ref[row.ref].add(row.chunk_id)
    facts = status_facts_on(conn, sorted({chunk_id for held in chunks_by_ref.values() for chunk_id in held}))
    found: dict[str, Holder] = {}
    for ref, held in chunks_by_ref.items():
        holder = holder_of({chunk_id: facts[chunk_id] for chunk_id in held if chunk_id in facts})
        if holder is not None:
            found[ref] = holder
    return found


_errand_runs_conforms: type[IReadErrandRunsRepository] = ErrandRunsStore
