"""SQLAlchemy adapter for the environment timeline's reads (package-private).

Read-only, over the route, transition, migration, lease, epoch-owner, escalation, usage, requeue, restart, terminal
and roster facts; every read is tenant-scoped through the store's statement builders, and per-chunk reads go one id
batch at a time (``bzh:bulk-reconstitution``)."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import ColumnElement

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
        created, envs, released_t = s.route_created, s.route_environments, s.route_released
        where: list[ColumnElement[bool]] = [] if until is None else [created.c.created_at < until]
        if chunk_id is not None:
            where.append(created.c.chunk_id == chunk_id)
        if project is not None:
            where.append(
                self._store.exists(
                    s.chunks, s.chunks.c.chunk_id == created.c.chunk_id, s.chunks.c.project_id == project
                )
            )
        if since is not None:
            # A route a later release of its chunk ended by ``since`` never reaches the window: the first release
            # after a route is no later than any after it. Nor does one its chunk ended, or reached its terminal node,
            # between the route's creation and ``since``.
            where.append(
                ~self._store.exists(
                    released_t,
                    released_t.c.chunk_id == created.c.chunk_id,
                    released_t.c.seq > created.c.seq,
                    released_t.c.released_at <= since,
                )
            )
            for table, col in ((s.chunk_completed, "completed_at"), (s.chunk_stopped, "stopped_at")):
                where.append(
                    ~self._store.exists(
                        table,
                        table.c.chunk_id == created.c.chunk_id,
                        table.c[col] >= created.c.created_at,
                        table.c[col] <= since,
                    )
                )
            tr = s.transitions
            where.append(
                ~self._store.exists(
                    tr,
                    tr.c.chunk_id == created.c.chunk_id,
                    tr.c.to_node_id == RESERVED_TERMINAL,
                    tr.c.recorded_at >= created.c.created_at,
                    tr.c.recorded_at <= since,
                )
            )
        with self._store.read("env_timeline.routes") as conn:
            rows = list(
                conn.execute(
                    self._store.select(
                        created,
                        created.c.route_id,
                        created.c.chunk_id,
                        created.c.runner_id,
                        created.c.workspace_id,
                        created.c.created_at,
                        created.c.seq,
                    ).where(*where)
                )
            )
            env_ids: dict[str, list[str]] = defaultdict(list)
            for batch in id_batches(sorted({row.route_id for row in rows})):
                for row in conn.execute(
                    self._store.select(envs, envs.c.route_id, envs.c.environment_id)
                    .where(envs.c.route_id.in_(batch))
                    .order_by(envs.c.route_id, envs.c.id)
                ):
                    env_ids[row.route_id].append(row.environment_id)
            released: list[RouteReleased] = []
            for batch in id_batches(sorted({row.chunk_id for row in rows})):
                released.extend(
                    RouteReleased(chunk_id=row.chunk_id, released_at=row.released_at, seq=row.seq)
                    for row in conn.execute(
                        self._store.select(
                            released_t, released_t.c.chunk_id, released_t.c.released_at, released_t.c.seq
                        ).where(released_t.c.chunk_id.in_(batch))
                    )
                )
        routes = [
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
        return routes, released

    def chunk_facts(self, chunk_ids: Sequence[str]) -> dict[str, ChunkTimelineFacts]:
        transitions: dict[str, list[StepTransition]] = defaultdict(list)
        migrations: dict[str, list[StepMigration]] = defaultdict(list)
        escalations: dict[str, list[StepEscalation]] = defaultdict(list)
        usage: dict[str, list[StepUsage]] = defaultdict(list)
        leases: dict[str, list[StepLease]] = defaultdict(list)
        endings: dict[str, list[datetime]] = defaultdict(list)
        requeues: dict[str, list[datetime]] = defaultdict(list)
        restarts: dict[str, list[datetime]] = defaultdict(list)
        titles: dict[str, str] = {}
        projects: dict[str, str] = {}
        graphs: dict[str, str] = {}
        tr, esc, use = s.transitions, s.escalations, s.usage_facts
        refs, items = s.chunk_work_refs, s.work_items
        with self._store.read("env_timeline.chunk_facts") as conn:
            for batch in id_batches(list(chunk_ids)):
                for row in conn.execute(
                    self._store.select(
                        tr,
                        tr.c.chunk_id,
                        tr.c.recorded_at,
                        tr.c.graph_id,
                        tr.c.from_node_id,
                        tr.c.to_node_id,
                        tr.c.choice_name,
                        tr.c.epoch,
                    ).where(tr.c.chunk_id.in_(batch))
                ):
                    transitions[row.chunk_id].append(
                        StepTransition(
                            row.recorded_at,
                            row.graph_id,
                            row.to_node_id,
                            row.choice_name,
                            row.epoch,
                            from_node_id=row.from_node_id,
                        )
                    )
                mg = s.chunk_migrations
                for row in conn.execute(
                    self._store.select(
                        mg,
                        mg.c.chunk_id,
                        mg.c.recorded_at,
                        mg.c.from_graph_id,
                        mg.c.to_graph_id,
                        mg.c.from_node_id,
                        mg.c.landed_node_id,
                        mg.c.choice_name,
                        mg.c.epoch,
                    ).where(mg.c.chunk_id.in_(batch))
                ):
                    migrations[row.chunk_id].append(
                        StepMigration(
                            row.recorded_at,
                            row.from_graph_id,
                            row.to_graph_id,
                            row.from_node_id,
                            row.landed_node_id,
                            row.choice_name,
                            row.epoch,
                        )
                    )
                # A mint is the hub's own when the hub owns its epoch: a runner's — a session's lease, or a forced
                # entry's takeover, which names none — is owned by its runner.
                lf, eo = s.lease_facts, s.epoch_owners
                owned = self._store.scope_join(
                    lf, eo, (eo.c.chunk_id == lf.c.chunk_id) & (eo.c.epoch == lf.c.epoch), isouter=True
                )
                for row in conn.execute(
                    self._store.select(
                        owned,
                        lf.c.chunk_id,
                        lf.c.epoch,
                        lf.c.lease_id,
                        lf.c.minted_at,
                        eo.c.id.label("owner_row"),
                        eo.c.runner_id.label("owner"),
                    ).where(lf.c.chunk_id.in_(batch))
                ):
                    hub = row.owner_row is not None and row.owner is None
                    leases[row.chunk_id].append(StepLease(row.epoch, row.lease_id, row.minted_at, hub=hub))
                for row in conn.execute(
                    self._store.select(
                        esc, esc.c.chunk_id, esc.c.recorded_at, esc.c.cause, esc.c.detail, esc.c.epoch
                    ).where(esc.c.chunk_id.in_(batch))
                ):
                    escalations[row.chunk_id].append(
                        StepEscalation(row.recorded_at, row.cause, row.detail, epoch=row.epoch)
                    )
                rq, rs = s.requeues, s.chunk_restarts
                for row in conn.execute(
                    self._store.select(rq, rq.c.chunk_id, rq.c.requeued_at).where(rq.c.chunk_id.in_(batch))
                ):
                    requeues[row.chunk_id].append(row.requeued_at)
                for row in conn.execute(
                    self._store.select(rs, rs.c.chunk_id, rs.c.recorded_at).where(rs.c.chunk_id.in_(batch))
                ):
                    restarts[row.chunk_id].append(row.recorded_at)
                for row in conn.execute(self._store.select(use).where(use.c.chunk_id.in_(batch))):
                    usage[row.chunk_id].append(
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
                        )
                    )
                for table, col in ((s.chunk_completed, "completed_at"), (s.chunk_stopped, "stopped_at")):
                    for row in conn.execute(
                        self._store.select(table, table.c.chunk_id, table.c[col]).where(table.c.chunk_id.in_(batch))
                    ):
                        endings[row.chunk_id].append(getattr(row, col))
                for row in conn.execute(
                    self._store.select(s.chunks, s.chunks.c.chunk_id, s.chunks.c.project_id, s.chunks.c.graph_id).where(
                        s.chunks.c.chunk_id.in_(batch)
                    )
                ):
                    projects[row.chunk_id] = row.project_id
                    graphs[row.chunk_id] = row.graph_id
                joined = self._store.scope_join(
                    refs, items, (refs.c.source == items.c.source) & (refs.c.ref == items.c.ref)
                )
                for row in conn.execute(
                    self._store.select(joined, refs.c.chunk_id, items.c.title)
                    .where(refs.c.chunk_id.in_(batch))
                    .order_by(refs.c.id)
                ):
                    titles.setdefault(row.chunk_id, row.title)
        return {
            cid: ChunkTimelineFacts(
                chunk_id=cid,
                title=titles.get(cid),
                project_id=projects.get(cid),
                graph_id=graphs.get(cid),
                endings=tuple(sorted(endings[cid])),
                transitions=tuple(transitions[cid]),
                migrations=tuple(migrations[cid]),
                escalations=tuple(escalations[cid]),
                usage=tuple(usage[cid]),
                leases=tuple(leases[cid]),
                requeues=tuple(sorted(requeues[cid])),
                restarts=tuple(sorted(restarts[cid])),
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
