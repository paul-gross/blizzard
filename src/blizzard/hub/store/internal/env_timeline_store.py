"""SQLAlchemy adapter for the environment timeline's reads (package-private).

Read-only. Every fact table it reads is an entry of :data:`TIMELINE_READERS`, tied to the registry's timeline
projection (``bzh:fact-families-declared``); the roster reads the runner and host tables outside it. Every read is
tenant-scoped through the store's statement builders, and per-chunk reads go one id batch at a time
(``bzh:bulk-reconstitution``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable, Iterator, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Any

from sqlalchemy import ColumnElement, Connection, Table

from blizzard.foundation.store.batching import id_batches
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.observability.env_timeline import (
    ChunkTimelineFacts,
    Roster,
    RouteCreated,
    RouteReleased,
    StepEscalation,
    StepLease,
    StepMigration,
    StepTransition,
    StepUsage,
    TimelineRunner,
)
from blizzard.hub.store import schema as s
from blizzard.hub.store.scoped import ScopedConnections

# --- The timeline's readers: one entry per fact table, each saying which read consumes it ------------------------

#: One chunk's fact rows from a table, id-batched: ``(chunk_id, item)`` pairs.
type _Rows = Iterator[tuple[str, Any]]
#: A predicate over the ``route_created`` row that holds when the chunk's fact ended the route by ``since``.
type _EndsRoute = Callable[[ScopedConnections, Table, datetime], ColumnElement[bool]]


#: The filters a window applies to the ``route_created`` row.
type _Narrow = Callable[[Table], list[ColumnElement[bool]]]


@dataclass(frozen=True)
class _ChunkFactsReader:
    """A table ``chunk_facts`` reads, id-batched by chunk, into the facts' ``field``."""

    field: str
    read: Callable[[ScopedConnections, Connection, Sequence[str]], _Rows]
    ends_route: _EndsRoute | None = None


@dataclass(frozen=True)
class _RouteWindowReader:
    """The table anchoring ``routes()``' window: reads the routes ``narrow`` leaves."""

    read: Callable[[ScopedConnections, Connection, _Narrow], list[RouteCreated]]


@dataclass(frozen=True)
class _RouteBatchReader:
    """A table ``routes()`` reads for the window's chunks, id-batched."""

    read: Callable[[ScopedConnections, Connection, Sequence[str]], list[RouteReleased]]
    ends_route: _EndsRoute | None = None


def _batched(
    table: Table, columns: Sequence[Any], item: Callable[[Any], Any], *, order_by: Any = None
) -> Callable[[ScopedConnections, Connection, Sequence[str]], _Rows]:
    """A reader of ``columns`` of ``table`` where its ``chunk_id`` is in the batch, each row mapped by ``item``."""

    def read(store: ScopedConnections, conn: Connection, batch: Sequence[str]) -> _Rows:
        statement = store.select(table, table.c.chunk_id, *columns).where(table.c.chunk_id.in_(batch))
        if order_by is not None:
            statement = statement.order_by(order_by)
        for row in conn.execute(statement):
            yield row.chunk_id, item(row)

    return read


def _ended_by(table: Table, column: str) -> _EndsRoute:
    """A chunk-ending fact (completed, stopped) recorded between the route's creation and ``since``."""

    def ends(store: ScopedConnections, created: Table, since: datetime) -> ColumnElement[bool]:
        return ~store.exists(
            table,
            table.c.chunk_id == created.c.chunk_id,
            table.c[column] >= created.c.created_at,
            table.c[column] <= since,
        )

    return ends


def _reached_terminal(store: ScopedConnections, created: Table, since: datetime) -> ColumnElement[bool]:
    tr = s.transitions
    return ~store.exists(
        tr,
        tr.c.chunk_id == created.c.chunk_id,
        tr.c.to_node_id == RESERVED_TERMINAL,
        tr.c.recorded_at >= created.c.created_at,
        tr.c.recorded_at <= since,
    )


def _released_by(store: ScopedConnections, created: Table, since: datetime) -> ColumnElement[bool]:
    # A route a later release of its chunk ended by ``since`` never reaches the window: the first release after a
    # route is no later than any after it.
    released = s.route_released
    return ~store.exists(
        released,
        released.c.chunk_id == created.c.chunk_id,
        released.c.seq > created.c.seq,
        released.c.released_at <= since,
    )


def _transitions() -> _ChunkFactsReader:
    tr = s.transitions
    return _ChunkFactsReader(
        "transitions",
        _batched(
            tr,
            (tr.c.recorded_at, tr.c.graph_id, tr.c.from_node_id, tr.c.to_node_id, tr.c.choice_name, tr.c.epoch),
            lambda row: StepTransition(
                row.recorded_at,
                row.graph_id,
                row.to_node_id,
                row.choice_name,
                row.epoch,
                from_node_id=row.from_node_id,
            ),
        ),
        ends_route=_reached_terminal,
    )


def _migrations() -> _ChunkFactsReader:
    mg = s.chunk_migrations
    return _ChunkFactsReader(
        "migrations",
        _batched(
            mg,
            (
                mg.c.recorded_at,
                mg.c.from_graph_id,
                mg.c.to_graph_id,
                mg.c.from_node_id,
                mg.c.landed_node_id,
                mg.c.choice_name,
                mg.c.epoch,
            ),
            lambda row: StepMigration(
                row.recorded_at,
                row.from_graph_id,
                row.to_graph_id,
                row.from_node_id,
                row.landed_node_id,
                row.choice_name,
                row.epoch,
            ),
        ),
    )


def _usage() -> _ChunkFactsReader:
    def read(store: ScopedConnections, conn: Connection, batch: Sequence[str]) -> _Rows:
        use = s.usage_facts
        for row in conn.execute(store.select(use).where(use.c.chunk_id.in_(batch))):
            yield (
                row.chunk_id,
                StepUsage(
                    row.node_id,
                    row.recorded_at,
                    row.cost_usd,
                    row.estimated_cost_usd,
                    model=row.model,
                    harness_id=row.harness_id,
                    input_tokens=row.input_tokens or 0,
                    output_tokens=row.output_tokens or 0,
                    cache_read_tokens=row.cache_read_tokens or 0,
                    cache_create_tokens=row.cache_create_tokens or 0,
                    kind=row.kind,
                    epoch=row.epoch,
                ),
            )

    return _ChunkFactsReader("usage", read)


def _titles() -> _ChunkFactsReader:
    def read(store: ScopedConnections, conn: Connection, batch: Sequence[str]) -> _Rows:
        refs, items = s.chunk_work_refs, s.work_items
        joined = store.scope_join(refs, items, (refs.c.source == items.c.source) & (refs.c.ref == items.c.ref))
        for row in conn.execute(
            store.select(joined, refs.c.chunk_id, items.c.title).where(refs.c.chunk_id.in_(batch)).order_by(refs.c.id)
        ):
            yield row.chunk_id, row.title

    return _ChunkFactsReader("titles", read)


def _routes_created(
    store: ScopedConnections,
    conn: Connection,
    narrow: _Narrow,
) -> list[RouteCreated]:
    created, envs = s.route_created, s.route_environments
    rows = list(
        conn.execute(
            store.select(
                created,
                created.c.route_id,
                created.c.chunk_id,
                created.c.runner_id,
                created.c.workspace_id,
                created.c.created_at,
                created.c.seq,
            ).where(*narrow(created))
        )
    )
    env_ids: dict[str, list[str]] = defaultdict(list)
    for batch in id_batches(sorted({row.route_id for row in rows})):
        for row in conn.execute(
            store.select(envs, envs.c.route_id, envs.c.environment_id)
            .where(envs.c.route_id.in_(batch))
            .order_by(envs.c.route_id, envs.c.id)
        ):
            env_ids[row.route_id].append(row.environment_id)
    return [
        RouteCreated(
            chunk_id=row.chunk_id,
            runner_id=row.runner_id,
            workspace_id=row.workspace_id,
            environment_ids=tuple(env_ids.get(row.route_id, ())),
            created_at=row.created_at,
            seq=row.seq,
        )
        for row in rows
    ]


def _routes_released(store: ScopedConnections, conn: Connection, batch: Sequence[str]) -> list[RouteReleased]:
    released = s.route_released
    return [
        RouteReleased(chunk_id=row.chunk_id, released_at=row.released_at, seq=row.seq)
        for row in conn.execute(
            store.select(released, released.c.chunk_id, released.c.released_at, released.c.seq).where(
                released.c.chunk_id.in_(batch)
            )
        )
    ]


#: Every fact table the timeline reads, with the read that consumes it; the registry's timeline projection names
#: the same tables (``tests/unit/hub/store/internal/test_fact_families.py``). A table a reader joins that is not
#: chunk-keyed (``work_items``, ``route_environments``) stays inside its fact table's reader.
TIMELINE_READERS: dict[Table, _ChunkFactsReader | _RouteWindowReader | _RouteBatchReader] = {
    s.transitions: _transitions(),
    s.chunk_migrations: _migrations(),
    s.lease_facts: _ChunkFactsReader(
        "leases",
        _batched(
            s.lease_facts,
            (s.lease_facts.c.epoch, s.lease_facts.c.lease_id, s.lease_facts.c.minted_at),
            lambda row: (row.epoch, row.lease_id, row.minted_at),
        ),
    ),
    s.epoch_owners: _ChunkFactsReader(
        "owners",
        _batched(
            s.epoch_owners, (s.epoch_owners.c.epoch, s.epoch_owners.c.runner_id), lambda row: (row.epoch, row.runner_id)
        ),
    ),
    s.escalations: _ChunkFactsReader(
        "escalations",
        _batched(
            s.escalations,
            (s.escalations.c.recorded_at, s.escalations.c.cause, s.escalations.c.detail, s.escalations.c.epoch),
            lambda row: StepEscalation(row.recorded_at, row.cause, row.detail, epoch=row.epoch),
        ),
    ),
    s.requeues: _ChunkFactsReader(
        "requeues", _batched(s.requeues, (s.requeues.c.requeued_at,), lambda row: row.requeued_at)
    ),
    s.chunk_restarts: _ChunkFactsReader(
        "restarts", _batched(s.chunk_restarts, (s.chunk_restarts.c.recorded_at,), lambda row: row.recorded_at)
    ),
    s.usage_facts: _usage(),
    s.chunk_completed: _ChunkFactsReader(
        "endings",
        _batched(s.chunk_completed, (s.chunk_completed.c.completed_at,), lambda row: row.completed_at),
        ends_route=_ended_by(s.chunk_completed, "completed_at"),
    ),
    s.chunk_stopped: _ChunkFactsReader(
        "endings",
        _batched(s.chunk_stopped, (s.chunk_stopped.c.stopped_at,), lambda row: row.stopped_at),
        ends_route=_ended_by(s.chunk_stopped, "stopped_at"),
    ),
    s.chunk_work_refs: _titles(),
    s.route_created: _RouteWindowReader(_routes_created),
    s.route_released: _RouteBatchReader(_routes_released, ends_route=_released_by),
}


def _ends_route() -> list[_EndsRoute]:
    return [r.ends_route for r in TIMELINE_READERS.values() if not isinstance(r, _RouteWindowReader) and r.ends_route]


class EnvTimelineStore:
    """Read-only environment-timeline adapter over the hub store engine."""

    def __init__(self, store: ScopedConnections) -> None:
        self._store = store

    def routes(
        self,
        *,
        since: datetime | None,
        until: datetime | None,
        chunk_id: str | None = None,
        project: str | None = None,
    ) -> tuple[list[RouteCreated], list[RouteReleased]]:
        window = next(r for r in TIMELINE_READERS.values() if isinstance(r, _RouteWindowReader))
        batch_reader = next(r for r in TIMELINE_READERS.values() if isinstance(r, _RouteBatchReader))

        def narrow(created: Table) -> list[ColumnElement[bool]]:
            narrowed = [] if until is None else [created.c.created_at < until]
            if chunk_id is not None:
                narrowed.append(created.c.chunk_id == chunk_id)
            if project is not None:
                narrowed.append(
                    self._store.exists(
                        s.chunks, s.chunks.c.chunk_id == created.c.chunk_id, s.chunks.c.project_id == project
                    )
                )
            if since is not None:
                # A route its chunk's release, ending, or terminal transition ended by ``since`` never reaches the
                # window.
                narrowed.extend(ends(self._store, created, since) for ends in _ends_route())
            return narrowed

        with self._store.read("env_timeline.routes") as conn:
            routes = window.read(self._store, conn, narrow)
            released: list[RouteReleased] = []
            for batch in id_batches(sorted({route.chunk_id for route in routes})):
                released.extend(batch_reader.read(self._store, conn, batch))
        return routes, released

    def chunk_facts(self, chunk_ids: Sequence[str]) -> dict[str, ChunkTimelineFacts]:
        read: dict[str, dict[str, list[Any]]] = defaultdict(lambda: defaultdict(list))
        projects: dict[str, str] = {}
        graphs: dict[str, str] = {}
        readers = [r for r in TIMELINE_READERS.values() if isinstance(r, _ChunkFactsReader)]
        with self._store.read("env_timeline.chunk_facts") as conn:
            for batch in id_batches(list(chunk_ids)):
                for reader in readers:
                    for cid, item in reader.read(self._store, conn, batch):
                        read[reader.field][cid].append(item)
                for row in conn.execute(
                    self._store.select(s.chunks, s.chunks.c.chunk_id, s.chunks.c.project_id, s.chunks.c.graph_id).where(
                        s.chunks.c.chunk_id.in_(batch)
                    )
                ):
                    projects[row.chunk_id] = row.project_id
                    graphs[row.chunk_id] = row.graph_id
        # A mint is the hub's own when the hub owns its epoch: a runner's — a session's lease, or a forced entry's
        # takeover, which names none — is owned by its runner. ``epoch_owners`` is unique on (chunk, epoch).
        owners = {(cid, epoch): runner for cid, rows in read["owners"].items() for epoch, runner in rows}
        return {
            cid: ChunkTimelineFacts(
                chunk_id=cid,
                title=next(iter(read["titles"][cid]), None),
                project_id=projects.get(cid),
                graph_id=graphs.get(cid),
                endings=tuple(sorted(read["endings"][cid])),
                transitions=tuple(read["transitions"][cid]),
                migrations=tuple(read["migrations"][cid]),
                escalations=tuple(read["escalations"][cid]),
                usage=tuple(read["usage"][cid]),
                leases=tuple(
                    StepLease(epoch, lease_id, minted_at, hub=owners.get((cid, epoch), "") is None)
                    for epoch, lease_id, minted_at in read["leases"][cid]
                ),
                requeues=tuple(sorted(read["requeues"][cid])),
                restarts=tuple(sorted(read["restarts"][cid])),
            )
            for cid in chunk_ids
            if cid in projects
        }

    def roster(self) -> Roster:
        runners, hosts = s.runner_registrations, s.hosts
        with self._store.read("env_timeline.roster") as conn:
            named = {
                row.runner_id: TimelineRunner(row.name or row.runner_id, row.host_id)
                for row in conn.execute(
                    self._store.select(runners, runners.c.runner_id, runners.c.name, runners.c.host_id)
                )
            }
            host_names = {
                row.host_id: row.name for row in conn.execute(self._store.select(hosts, hosts.c.host_id, hosts.c.name))
            }
        return Roster(runners=named, hosts=host_names)
