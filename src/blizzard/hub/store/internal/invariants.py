"""The hub store's facts-level invariants (``bzh:invariant-checker``): transitions, epochs, delivery, tenancy."""

from __future__ import annotations

from collections import Counter
from collections.abc import Callable, Generator
from contextlib import contextmanager
from dataclasses import dataclass
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Connection, CursorResult, Row, Select, Table, func, select

from blizzard.foundation.clock import IClock
from blizzard.foundation.invariants import Check, QueryCheck, Violation
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor
from blizzard.hub.domain.chunk.model import ChunkFacts, RouteHistory
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.delivery.flows import RECONCILE_FLOOR, last_moved_at, withdrawal_for
from blizzard.hub.domain.delivery.ports.flows import IReadFlows
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.store import schema as hub
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.chunk_rows import DEFAULT_MODEL
from blizzard.hub.store.scoped import TENANT_COLUMN, ScopedConnections, is_tenant_owned
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId

# The ``lease_facts.runner_id`` a hub mint records — its own lease row, never a runner's.
_HUB_LEASE_RUNNER_ID = "hub"


@dataclass(frozen=True)
class HubQueryCheck(QueryCheck):
    """A hub invariant judged over the open tenants' rows. A deletion drains a closed tenant's tables one after
    another, a fact before the fact or record it pairs with, so partway through what is left of the tenant is no
    longer a whole history; every read through :meth:`rows` leaves out each tenant a deletion has closed."""

    def rows(self, statement: Select[Any]) -> CursorResult[Any]:
        """``statement``'s rows of the open tenants only — each tenant-owned table it reads from is confined to the
        tenants no ``tenant_deletions`` row names."""
        closed = select(hub.tenant_deletions.c.tenant_id)
        open_only = [
            from_.c[TENANT_COLUMN].not_in(closed) for from_ in statement.get_final_froms() if is_tenant_owned(from_)
        ]
        return self.conn.execute(statement.where(*open_only) if open_only else statement)


@dataclass(frozen=True)
class FactsCheck(Check):
    """An invariant answerable only per chunk, over its loaded facts."""

    facts: IReadChunkFactsRepository
    record: IReadChunkRecordRepository

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        facts_by_id = self.facts.load_all_facts()
        for chunk in self.record.list_all():
            violations.extend(self.for_chunk(chunk.chunk_id, facts_by_id.get(chunk.chunk_id)))
        return violations

    def for_chunk(self, chunk_id: str, facts: ChunkFacts | None) -> list[Violation]:
        raise NotImplementedError


class OneTransitionPerNodeEpoch(HubQueryCheck):
    """At most one accepted transition per (chunk, from_node, epoch): the idempotency
    guarantee. A duplicate is a double-apply."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        key = Counter(
            (row[0], row[1], row[2])
            for row in self.rows(
                select(hub.transitions.c.chunk_id, hub.transitions.c.from_node_id, hub.transitions.c.epoch)
            )
        )
        for (chunk_id, from_node, epoch), n in key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:one-transition-per-node-epoch",
                        f"chunk {chunk_id} node {from_node} epoch {epoch} has {n} transitions",
                    )
                )
        return violations


class EpochConsistentTransitions(HubQueryCheck):
    """No accepted transition carries an epoch greater than the chunk's latest lease fact;
    a higher one means a zombie landed."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        latest_lease = {
            row[0]: row[1]
            for row in self.rows(
                select(hub.lease_facts.c.chunk_id, func.max(hub.lease_facts.c.epoch)).group_by(
                    hub.lease_facts.c.chunk_id
                )
            )
        }
        for chunk_id, max_epoch in self.rows(
            select(hub.transitions.c.chunk_id, func.max(hub.transitions.c.epoch)).group_by(hub.transitions.c.chunk_id)
        ):
            known = latest_lease.get(chunk_id)
            if known is None or max_epoch > known:
                violations.append(
                    Violation(
                        "hub:epoch-consistent-transitions",
                        f"chunk {chunk_id} transition epoch {max_epoch} exceeds latest lease {known}",
                    )
                )
        return violations


class EpochsOwned(HubQueryCheck):
    """Every epoch a lease fact or a restart took has an owner row, and that row names the
    epoch's first taker — the rule the owner backfill applied: restarts first, in id order,
    hub-owned, then lease facts in id order, a ``hub`` lease hub-owned. A different first
    taker means a displaced attempt's mint landed at an epoch reserved for another owner."""

    def run(self) -> list[Violation]:
        owners = {
            (row.chunk_id, row.epoch): row.runner_id
            for row in self.rows(
                select(hub.epoch_owners.c.chunk_id, hub.epoch_owners.c.epoch, hub.epoch_owners.c.runner_id)
            )
        }
        leases = hub.lease_facts.c
        restarts = hub.chunk_restarts.c
        first_taker: dict[tuple[str, int], str | None] = {}
        for chunk_id, epoch in self.rows(select(restarts.chunk_id, restarts.epoch).order_by(restarts.id)):
            first_taker.setdefault((chunk_id, epoch), None)
        for chunk_id, epoch, runner_id in self.rows(
            select(leases.chunk_id, leases.epoch, leases.runner_id).order_by(leases.id)
        ):
            first_taker.setdefault((chunk_id, epoch), None if runner_id == _HUB_LEASE_RUNNER_ID else runner_id)
        violations: list[Violation] = []
        for (chunk_id, epoch), taker in first_taker.items():
            if (chunk_id, epoch) not in owners:
                violations.append(Violation("hub:epochs-owned", f"chunk {chunk_id} epoch {epoch} has no owner"))
            elif owners[(chunk_id, epoch)] != taker:
                violations.append(
                    Violation(
                        "hub:epochs-owned",
                        f"chunk {chunk_id} epoch {epoch} taken by {taker or 'the hub'} but owned by "
                        f"{owners[(chunk_id, epoch)] or 'the hub'}",
                    )
                )
        return violations


class RouteSeqUnique(HubQueryCheck):
    """Per-chunk route ``seq`` is unique across ``route_created`` + ``route_released`` +
    ``route_token_minted`` combined."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        route_seqs = Counter(
            (row[0], row[1]) for row in self.rows(select(hub.route_created.c.chunk_id, hub.route_created.c.seq))
        )
        route_seqs.update(
            (row[0], row[1]) for row in self.rows(select(hub.route_released.c.chunk_id, hub.route_released.c.seq))
        )
        route_seqs.update(
            (row[0], row[1])
            for row in self.rows(select(hub.route_token_minted.c.chunk_id, hub.route_token_minted.c.seq))
        )
        for (chunk_id, seq), n in route_seqs.items():
            if n > 1:
                violations.append(
                    Violation("hub:route-seq-unique", f"chunk {chunk_id} seq {seq} used by {n} route events")
                )
        return violations


class OneLiveRoutePerChunk(HubQueryCheck):
    """At most one live route per chunk — walking each chunk's ``route_created``/
    ``route_released`` facts in ``(timestamp, seq)`` order (:class:`RouteHistory`'s own
    tie-break), no ``route_created`` follows another with no ``route_released`` between.
    ``bzh:store-exclusive-write``'s row lock is what keeps this true on Postgres."""

    def run(self) -> list[Violation]:
        events: dict[str, list[tuple[datetime, int, str]]] = {}
        for row in self.rows(
            select(hub.route_created.c.chunk_id, hub.route_created.c.created_at, hub.route_created.c.seq)
        ):
            events.setdefault(row.chunk_id, []).append((row.created_at, row.seq, "created"))
        for row in self.rows(
            select(hub.route_released.c.chunk_id, hub.route_released.c.released_at, hub.route_released.c.seq)
        ):
            events.setdefault(row.chunk_id, []).append((row.released_at, row.seq, "released"))
        violations: list[Violation] = []
        for chunk_id, chunk_events in events.items():
            chunk_events.sort(key=lambda e: (e[0], e[1]))
            live = False
            for _, _, kind in chunk_events:
                if kind == "created":
                    if live:
                        violations.append(
                            Violation(
                                "hub:one-live-route-per-chunk",
                                f"chunk {chunk_id} has a route_created fact with no route_released before the next",
                            )
                        )
                    live = True
                else:
                    live = False
        return violations


class PerRepoLandIdempotent(HubQueryCheck):
    """A redelivery skips already-landed repos, so a second landed fact is a double land."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        repo_lands = Counter(
            (row[0], row[1])
            for row in self.rows(select(hub.delivery_repo_landed.c.chunk_id, hub.delivery_repo_landed.c.repo))
        )
        for (chunk_id, repo), n in repo_lands.items():
            if n > 1:
                violations.append(
                    Violation("hub:per-repo-land-idempotent", f"chunk {chunk_id} repo {repo} landed {n} times")
                )
        return violations


class PerRepoMarkerIdempotent(HubQueryCheck):
    """At most one ``merged/<repo>`` marker artifact per (chunk, node, epoch, name)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        markers = Counter(
            (row[0], row[1], row[2], row[3])
            for row in self.rows(
                select(
                    hub.artifacts.c.chunk_id, hub.artifacts.c.node_id, hub.artifacts.c.epoch, hub.artifacts.c.name
                ).where(hub.artifacts.c.name.like("merged/%"))
            )
        )
        for (chunk_id, node_id, epoch, name), n in markers.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:per-repo-marker-idempotent",
                        f"chunk {chunk_id} node {node_id} epoch {epoch} has {n} `{name}` marker artifacts",
                    )
                )
        return violations


class NoDoubleDelivery(HubQueryCheck):
    """At most one whole-chunk delivery.landed terminal fact."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        landed = Counter(row[0] for row in self.rows(select(hub.delivery_landed.c.chunk_id)))
        for chunk_id, n in landed.items():
            if n > 1:
                violations.append(
                    Violation("hub:no-double-delivery", f"chunk {chunk_id} has {n} delivery.landed facts")
                )
        return violations


class NoDoubleTerminalClosure(HubQueryCheck):
    """At most one terminal (``closed``/``gone``/``skipped``) ``work_item_closures`` outcome per
    ``(chunk, source, ref)`` — the drain's idempotency guard is a
    ``retired_at`` check, not a store-level constraint, so a bug there would otherwise
    go unseen: the schema itself permits ``closed`` and ``gone`` to coexist."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        key = Counter(
            (row.chunk_id, row.source, row.ref)
            for row in self.rows(
                select(
                    hub.work_item_closures.c.chunk_id, hub.work_item_closures.c.source, hub.work_item_closures.c.ref
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone", "skipped"]))
            )
        )
        for (chunk_id, source, ref), n in key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:no-double-terminal-closure",
                        f"chunk {chunk_id} ref {source}#{ref} has {n} terminal closure outcomes",
                    )
                )
        return violations


class NoPendingIntentAgainstATerminalRef(HubQueryCheck):
    """A pending (``retired_at IS NULL``) ``close_intents`` row whose ``(chunk, source,
    ref)`` already carries a terminal closure outcome is a stuck retirement — the drain
    recorded the outcome but never retired the intent that rode it."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        terminal = {
            (row.chunk_id, row.source, row.ref)
            for row in self.rows(
                select(
                    hub.work_item_closures.c.chunk_id, hub.work_item_closures.c.source, hub.work_item_closures.c.ref
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone", "skipped"]))
            )
        }
        for row in self.rows(
            select(hub.close_intents.c.chunk_id, hub.close_intents.c.source, hub.close_intents.c.ref).where(
                hub.close_intents.c.retired_at.is_(None)
            )
        ):
            if (row.chunk_id, row.source, row.ref) in terminal:
                violations.append(
                    Violation(
                        "hub:no-pending-intent-against-terminal-ref",
                        f"chunk {row.chunk_id} ref {row.source}#{row.ref} has a pending close intent "
                        "but a terminal closure outcome",
                    )
                )
        return violations


class NoUnenqueuedClosableRef(HubQueryCheck):
    """A non-ephemeral chunk that has landed or been hand-completed owes a ``close_intents``
    row or terminal skip fact for every still-open ``chunk_work_refs`` ref — the
    shared enqueue write's derived invariant. A terminal
    outcome or any ``close_intents`` row (retired or not) satisfies it; neither is a missed call."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        ephemeral = {row[0] for row in self.rows(select(hub.chunk_grouped.c.chunk_id))} | {
            row[0] for row in self.rows(select(hub.chunk_deleted.c.chunk_id))
        }
        landed = (
            {row[0] for row in self.rows(select(hub.delivery_landed.c.chunk_id))}
            | {row[0] for row in self.rows(select(hub.delivery_repo_landed.c.chunk_id))}
            | {
                row[0]
                for row in self.rows(select(hub.artifacts.c.chunk_id).where(hub.artifacts.c.name.like("merged/%")))
            }
        )
        completed = {row[0] for row in self.rows(select(hub.chunk_completed.c.chunk_id))}
        eligible = (landed | completed) - ephemeral
        if not eligible:
            return violations
        terminal = {
            (row.chunk_id, row.source, row.ref)
            for row in self.rows(
                select(
                    hub.work_item_closures.c.chunk_id, hub.work_item_closures.c.source, hub.work_item_closures.c.ref
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone", "skipped"]))
            )
        }
        enqueued = {
            (row.chunk_id, row.source, row.ref)
            for row in self.rows(
                select(hub.close_intents.c.chunk_id, hub.close_intents.c.source, hub.close_intents.c.ref)
            )
        }
        for row in self.rows(
            select(hub.chunk_work_refs.c.chunk_id, hub.chunk_work_refs.c.source, hub.chunk_work_refs.c.ref).where(
                hub.chunk_work_refs.c.chunk_id.in_(list(eligible))
            )
        ):
            key = (row.chunk_id, row.source, row.ref)
            if key in terminal or key in enqueued:
                continue
            violations.append(
                Violation(
                    "hub:no-unenqueued-closable-ref",
                    f"chunk {row.chunk_id} ref {row.source}#{row.ref} is landed or completed but has "
                    "no close_intents row and no terminal closure outcome",
                )
            )
        return violations


class OneTurnPerFlow(HubQueryCheck):
    """``hub:one-turn-per-flow`` — no flow has two open turns (``ended_at IS NULL``) holding it at once."""

    def run(self) -> list[Violation]:
        turns, held = hub.hub_turns, hub.flow_submission_flows
        open_turns = func.count()
        crowded = self.rows(
            select(held.c.tenant_id, held.c.flow_id, open_turns)
            .join(turns, turns.c.submission_id == held.c.submission_id)
            .where(turns.c.ended_at.is_(None))
            .group_by(held.c.tenant_id, held.c.flow_id)
            .having(open_turns > 1)
            .order_by(held.c.tenant_id, held.c.flow_id)
        ).all()
        return [
            Violation("hub:one-turn-per-flow", f"tenant {row.tenant_id}: flow {row.flow_id} has {row[2]} open turns")
            for row in crowded
        ]


class OneTurnPerVisit(HubQueryCheck):
    """``hub:one-turn-per-visit`` — no submission has two open turns at once."""

    def run(self) -> list[Violation]:
        turns = hub.hub_turns
        open_turns = func.count()
        crowded = self.rows(
            select(turns.c.tenant_id, turns.c.submission_id, open_turns)
            .where(turns.c.ended_at.is_(None))
            .group_by(turns.c.tenant_id, turns.c.submission_id)
            .having(open_turns > 1)
            .order_by(turns.c.tenant_id, turns.c.submission_id)
        ).all()
        return [
            Violation(
                "hub:one-turn-per-visit",
                f"tenant {row.tenant_id}: submission {row.submission_id} has {row[2]} open turns",
            )
            for row in crowded
        ]


@dataclass(frozen=True)
class FlowLineStanding(Check):
    """``hub:flow-line-standing`` — every open submission's chunk still stands where its line holds it: at its node,
    at its epoch, or at a later one while a person holds the chunk — ``withdrawal_for`` answering nothing. The line
    converges on two kinds by design, so both are exempt: one with an open turn, whose end writes its closure, and
    one whose chunk moved within :data:`RECONCILE_FLOOR`, the longest a full reconcile is ever skipped."""

    flows: IReadFlows
    facts: IReadChunkFactsRepository
    clock: IClock

    def run(self) -> list[Violation]:
        turning = {turn.submission_id for turn in self.flows.open_turns()}
        submissions = [sub for sub in self.flows.open_submissions_from(0) if sub.submission_id not in turning]
        facts_by_id = self.facts.load_facts_for(sorted({sub.chunk_id for sub in submissions}))
        settled_before = self.clock.now() - RECONCILE_FLOOR
        violations: list[Violation] = []
        for sub in submissions:
            facts = ChunkFacts.or_default(facts_by_id.get(sub.chunk_id))
            withdrawal = withdrawal_for(facts, sub)
            moved = last_moved_at(facts)
            if withdrawal is None or (moved is not None and moved > settled_before):
                continue
            violations.append(
                Violation(
                    "hub:flow-line-standing",
                    f"submission {sub.submission_id} of chunk {sub.chunk_id} at node {sub.node_id} epoch {sub.epoch} "
                    f"is still open, but its chunk has left the visit ({withdrawal})",
                )
            )
        return violations


class HubNodeHasAction(HubQueryCheck):
    """``hub:hub-node-has-action`` — every hub node of a graph a live chunk is pinned to names an action. A node
    naming none is one the carry-over could not translate, and a chunk reaching it escalates to a person. A chunk
    is live here while no stop, completion, deletion, grouping, or transition into the reserved terminal names it — a
    floor: a terminal transition a later restart superseded reads as ended."""

    def run(self) -> list[Violation]:
        nodes, chunks = hub.graph_nodes, hub.chunks
        ended = [
            select(table.c.chunk_id)
            for table in (hub.chunk_stopped, hub.chunk_completed, hub.chunk_deleted, hub.chunk_grouped)
        ]
        ended.append(select(hub.transitions.c.chunk_id).where(hub.transitions.c.to_node_id == RESERVED_TERMINAL))
        pinned = select(chunks.c.graph_id).where(*(chunks.c.chunk_id.not_in(q) for q in ended))
        rows = self.rows(
            select(nodes.c.tenant_id, nodes.c.graph_id, nodes.c.name)
            .where(
                nodes.c.executor == Executor.HUB.value,
                nodes.c.action.is_(None),
                nodes.c.graph_id.in_(pinned),
            )
            .order_by(nodes.c.tenant_id, nodes.c.graph_id, nodes.c.name)
        ).all()
        return [
            Violation(
                "hub:hub-node-has-action",
                f"tenant {row.tenant_id}: graph {row.graph_id}'s hub node {row.name!r} names no action, "
                "and a live chunk is pinned to the graph",
            )
            for row in rows
        ]


class NoRowOfADeletedTenant(QueryCheck):
    """``hub:no-row-of-a-deleted-tenant`` — a tenant's deletion removes every row it owns before it
    removes the tenant, and its memberships with it, so no row naming a tenant — of a tenant-owned table, or of a
    global one recording a tenant's id — names one absent from ``tenants``. It reads every tenant, a closed one
    included: what a drain leaves behind is exactly what it looks for."""

    def run(self) -> list[Violation]:
        present = select(hub.tenants.c.tenant_id)
        violations: list[Violation] = []
        for table in hub.metadata.sorted_tables:
            if not _names_a_tenant(table):
                continue
            tenant, rows = table.c.tenant_id, func.count()
            orphaned = self.conn.execute(
                select(tenant, rows).where(tenant.not_in(present)).group_by(tenant).order_by(tenant)
            ).all()
            violations.extend(
                Violation(
                    "hub:no-row-of-a-deleted-tenant",
                    f"{table.name} holds {row[1]} row(s) of tenant {row.tenant_id}, which is not in tenants",
                )
                for row in orphaned
            )
        return violations


#: The tenants' own registry: a closed tenant's deletion row outlives the tenant by design.
_TENANT_REGISTRY = frozenset({hub.tenants.name, hub.tenant_deletions.name})


def _names_a_tenant(table: Table) -> bool:
    """Whether ``table``'s rows each name a tenant: a tenant-owned table, or a global one carrying a ``tenant_id``
    outside the tenants' own registry."""
    return is_tenant_owned(table) or (TENANT_COLUMN in table.c and table.name not in _TENANT_REGISTRY)


class MigrationsAtomic(HubQueryCheck):
    """``hub:one-migration-per-node-epoch`` — one row per ``(chunk, from_node, epoch)``;
    ``hub:migration-pin-consistent`` — the chunk carries the newest migration's target pin;
    ``hub:migration-route-released`` — a runner landing released the route (a hub landing
    and an operator restart's own re-pin, #371, both keep it by design)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        key = Counter(
            (row[0], row[1], row[2])
            for row in self.rows(
                select(
                    hub.chunk_migrations.c.chunk_id,
                    hub.chunk_migrations.c.from_node_id,
                    hub.chunk_migrations.c.epoch,
                )
            )
        )
        for (chunk_id, from_node, epoch), n in key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:one-migration-per-node-epoch",
                        f"chunk {chunk_id} node {from_node} epoch {epoch} has {n} migrations",
                    )
                )

        newest: dict[str, Row[Any]] = {}
        for m in self.rows(select(hub.chunk_migrations)):
            cur = newest.get(m.chunk_id)
            if cur is None or (m.recorded_at, m.epoch) >= (cur.recorded_at, cur.epoch):
                newest[m.chunk_id] = m
        chunks = {c.chunk_id: c for c in self.rows(select(hub.chunks))}
        # A migration's landed node executor. Node ids are globally-unique, so
        # one node_id -> executor map resolves any landing node.
        landed_executor = {
            row.node_id: row.executor
            for row in self.rows(select(hub.graph_nodes.c.node_id, hub.graph_nodes.c.executor))
        }
        # The latest route release per chunk — a runner-landing migration's ``recorded_at``
        # is never above the chunk's newest release.
        latest_release: dict[str, datetime] = {}
        for r in self.rows(select(hub.route_released.c.chunk_id, hub.route_released.c.released_at)):
            cur = latest_release.get(r.chunk_id)
            if cur is None or r.released_at > cur:
                latest_release[r.chunk_id] = r.released_at
        for chunk_id, m in newest.items():
            chunk = chunks.get(chunk_id)
            if chunk is None:
                continue
            if chunk.graph_id != m.to_graph_id:
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} pinned {chunk.graph_id} but its newest migration targets {m.to_graph_id}",
                    )
                )
            # **Membership**, not equality against `[model_after]` — the list may
            # legitimately grow afterwards (pinned by tests/test_invariant_checker.py).
            elif m.model_after is not None and m.model_after not in DEFAULT_MODEL.decode(chunk.default_model):
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} default_model {chunk.default_model!r} does not carry "
                        f"{m.model_after}, which its newest migration re-pinned",
                    )
                )
            # A hub landing and an operator restart's own re-pin (#371) both retain the
            # route by design — neither is a torn write, so neither owes a release.
            keeps_route = (
                landed_executor.get(m.landed_node_id) == Executor.HUB or m.source == MigrationSource.RESTART.value
            )
            released = latest_release.get(chunk_id)
            if not keeps_route and (released is None or released < m.recorded_at):
                violations.append(
                    Violation(
                        "hub:migration-route-released",
                        f"chunk {chunk_id} migrated at {m.recorded_at} but no route release landed "
                        "with it — a torn migrate. write kept the stale claim",
                    )
                )
        return violations


class DerivationAndDelivery(FactsCheck):
    """``hub:derived-status-total`` — every chunk derives exactly one status without panic;
    ``hub:merge-queue-single-state`` — a delivered chunk's newest transition is the terminal."""

    def for_chunk(self, chunk_id: str, facts: ChunkFacts | None) -> list[Violation]:
        if facts is None:
            return [Violation("hub:derived-status-total", f"chunk {chunk_id} has no loadable facts")]
        try:
            facts.status()
        except Exception as exc:  # a fact combination the derivation cannot resolve
            return [Violation("hub:derived-status-total", f"chunk {chunk_id} derivation raised {exc!r}")]
        # ``delivery.landed`` requires the terminal transition.
        if facts.delivery_landed:
            newest = max(facts.transitions, key=lambda t: (t.recorded_at, t.epoch), default=None)
            if newest is None or newest.to_node_id != RESERVED_TERMINAL:
                target = None if newest is None else newest.to_node_id
                return [
                    Violation(
                        "hub:merge-queue-single-state",
                        f"chunk {chunk_id} is delivery.landed but newest transition targets {target}",
                    )
                ]
        return []


class LiveRouteHasToken(FactsCheck):
    """A live route with no qualifying token fact means the mint never landed in the same
    store write as its route. That leaves every chunk-scoped write for the chunk
    permanently rejected under ``route_token_mode=enforce``, with no re-key possible."""

    def for_chunk(self, chunk_id: str, facts: ChunkFacts | None) -> list[Violation]:
        if facts is None:
            return []
        routes = RouteHistory.of(facts)
        if routes.newest is None:
            return []
        if routes.newest_token is None:
            return [Violation("hub:live-route-has-token", f"chunk {chunk_id} has a live route but no live token")]
        return []


@dataclass(frozen=True)
class SegmentChunkResolves(HubQueryCheck):
    """Every ``transcript_segments.chunk_id`` resolves to a ``chunks`` row — the foreign key
    the table declares, which sqlite does not enforce. A breach hides a chunk's transcripts
    from every read that joins through the chunk, so transcript-event derivation declines the
    segment rather than deriving a node-step it cannot place."""

    def run(self) -> list[Violation]:
        minted = {row[0] for row in self.rows(select(hub.chunks.c.chunk_id))}
        orphans = {
            (row.segment_id, row.chunk_id)
            for row in self.rows(
                select(hub.transcript_segments.c.segment_id, hub.transcript_segments.c.chunk_id).distinct()
            )
            if row.chunk_id not in minted
        }
        return [
            Violation("hub:segment-chunk-resolves", f"segment {segment_id} names unknown chunk {chunk_id}")
            for segment_id, chunk_id in sorted(orphans)
        ]


class NoStandingDependencyCycle(HubQueryCheck):
    """The standing (unreleased) ``chunk_dependencies`` edges form no cycle — a derived
    cross-fact invariant with no engine constraint behind it, held only by
    ``DependencyService`` under the claim lock."""

    def run(self) -> list[Violation]:
        graph: dict[str, list[str]] = {}
        for row in self.rows(
            select(hub.chunk_dependencies.c.dependent_chunk_id, hub.chunk_dependencies.c.prerequisite_chunk_id).where(
                hub.chunk_dependencies.c.released_at.is_(None)
            )
        ):
            graph.setdefault(row.dependent_chunk_id, []).append(row.prerequisite_chunk_id)

        violations: list[Violation] = []
        for start in sorted(graph):
            if self._reaches(graph, start, start):
                violations.append(
                    Violation(
                        "hub:no-standing-dependency-cycle",
                        f"chunk {start} is on a cycle in the standing dependency graph",
                    )
                )
        return violations

    @staticmethod
    def _reaches(graph: dict[str, list[str]], node: str, target: str) -> bool:
        frontier = list(graph.get(node, []))
        seen = set(frontier)
        while frontier:
            current = frontier.pop()
            if current == target:
                return True
            for neighbor in graph.get(current, []):
                if neighbor not in seen:
                    seen.add(neighbor)
                    frontier.append(neighbor)
        return False


class NoDuplicateStandingDependency(HubQueryCheck):
    """At most one standing (unreleased) edge per ordered ``(dependent, prerequisite)``
    pair — a durable invariant held only by domain code under the claim lock, since
    ``chunk_dependencies`` carries no database uniqueness constraint on the pair."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        pairs = Counter(
            (row.dependent_chunk_id, row.prerequisite_chunk_id)
            for row in self.rows(
                select(
                    hub.chunk_dependencies.c.dependent_chunk_id, hub.chunk_dependencies.c.prerequisite_chunk_id
                ).where(hub.chunk_dependencies.c.released_at.is_(None))
            )
        )
        for (dependent_chunk_id, prerequisite_chunk_id), n in pairs.items():
            if n > 1:
                violations.append(
                    Violation(
                        "hub:no-duplicate-standing-dependency",
                        f"chunk {dependent_chunk_id} has {n} standing dependencies on {prerequisite_chunk_id}",
                    )
                )
        return violations


class NoStandingDependencyOntoEphemeralChunk(HubQueryCheck):
    """A standing (unreleased) edge whose dependent or prerequisite is ephemeral (grouped away or deleted) — its FK
    still resolves, so the engine enforces nothing here. Design upholds this by construction now that
    ``DeleteService`` and ``GroupService`` both hold their own row lock over the chunks they touch and share the
    residual fleet-wide cycle lock; this check is the backstop that would catch a regression, not a guard against a
    known-live gap."""

    def run(self) -> list[Violation]:
        ephemeral = {row[0] for row in self.rows(select(hub.chunk_grouped.c.chunk_id))} | {
            row[0] for row in self.rows(select(hub.chunk_deleted.c.chunk_id))
        }
        if not ephemeral:
            return []
        violations: list[Violation] = []
        for row in self.rows(
            select(hub.chunk_dependencies.c.dependent_chunk_id, hub.chunk_dependencies.c.prerequisite_chunk_id).where(
                hub.chunk_dependencies.c.released_at.is_(None)
            )
        ):
            for role, chunk_id in (("dependent", row.dependent_chunk_id), ("prerequisite", row.prerequisite_chunk_id)):
                if chunk_id in ephemeral:
                    violations.append(
                        Violation(
                            "hub:no-standing-dependency-onto-ephemeral-chunk",
                            f"standing dependency {row.dependent_chunk_id} on {row.prerequisite_chunk_id} "
                            f"names ephemeral {role} {chunk_id}",
                        )
                    )
        return violations


def _project_label(project_id: str | None) -> str:
    return f"project {project_id}" if project_id is not None else "no project"


def _same_project(one: str | None, other: str | None) -> bool:
    """Whether both sides name a project and it is the same one; a side that resolves to none never matches."""
    return one is not None and one == other


class ProjectOfItsTenant(HubQueryCheck):
    """Every row of :attr:`table` names, by its ``project_id``, a project of the row's own tenant. The foreign key
    says only that the project exists, not whose it is, so only a read can show a row filed under another tenant's
    project."""

    invariant: ClassVar[str]
    table: ClassVar[Table]
    key: ClassVar[str]
    noun: ClassVar[str]

    def run(self) -> list[Violation]:
        projects = hub.projects
        tenant_of = {
            row.project_id: row.tenant_id for row in self.rows(select(projects.c.project_id, projects.c.tenant_id))
        }
        key = self.table.c[self.key]
        return [
            Violation(
                self.invariant,
                f"{self.noun} {row[0]} of tenant {row.tenant_id} names project {row.project_id}, "
                "not a project of its tenant",
            )
            for row in self.rows(select(key, self.table.c.tenant_id, self.table.c.project_id).order_by(key))
            if tenant_of.get(row.project_id) != row.tenant_id
        ]


class ChunkProjectInTenant(ProjectOfItsTenant):
    """``hub:chunk-project-in-tenant`` — every chunk's project is one of the chunk's tenant's."""

    invariant = "hub:chunk-project-in-tenant"
    table = hub.chunks
    key = "chunk_id"
    noun = "chunk"


class WorkItemProjectInTenant(ProjectOfItsTenant):
    """``hub:work-item-project-in-tenant`` — every work item's project is one of the item's tenant's."""

    invariant = "hub:work-item-project-in-tenant"
    table = hub.work_items
    key = "work_item_id"
    noun = "work item"


def _scope_projects(check: HubQueryCheck) -> dict[str, str]:
    scopes = hub.scopes
    return {row.scope_id: row.project_id for row in check.rows(select(scopes.c.scope_id, scopes.c.project_id))}


class RoutineScopeSameProject(HubQueryCheck):
    """``hub:routine-scope-same-project`` — a routine's default scope, and every scope a ``routine_scopes`` row gives
    it, belong to the routine's own project: a routine runs only over its project's scopes."""

    def run(self) -> list[Violation]:
        scope_project = _scope_projects(self)
        routines, members = hub.routines, hub.routine_scopes
        routine_project: dict[str, str] = {}
        violations: list[Violation] = []
        for row in self.rows(
            select(routines.c.routine_id, routines.c.project_id, routines.c.default_scope_id).order_by(
                routines.c.routine_id
            )
        ):
            routine_project[row.routine_id] = row.project_id
            if not _same_project(scope_project.get(row.default_scope_id), row.project_id):
                violations.append(
                    Violation(
                        "hub:routine-scope-same-project",
                        f"routine {row.routine_id} of project {row.project_id} defaults to scope "
                        f"{row.default_scope_id} of {_project_label(scope_project.get(row.default_scope_id))}",
                    )
                )
        for row in self.rows(
            select(members.c.routine_id, members.c.scope_id).order_by(members.c.routine_id, members.c.scope_id)
        ):
            routine_of, scope_of = routine_project.get(row.routine_id), scope_project.get(row.scope_id)
            if not _same_project(routine_of, scope_of):
                violations.append(
                    Violation(
                        "hub:routine-scope-same-project",
                        f"routine {row.routine_id} of {_project_label(routine_of)} is scoped to scope "
                        f"{row.scope_id} of {_project_label(scope_of)}",
                    )
                )
        return violations


class FindingScopeInProject(HubQueryCheck):
    """``hub:finding-scope-in-project`` — every finding and every finding set names a scope of its own project."""

    def run(self) -> list[Violation]:
        scope_project = _scope_projects(self)
        violations: list[Violation] = []
        for noun, table, key in (
            ("finding", hub.findings, hub.findings.c.finding_id),
            ("finding set", hub.finding_sets, hub.finding_sets.c.finding_set_id),
        ):
            for row in self.rows(select(key, table.c.scope_id, table.c.project_id).order_by(key)):
                if not _same_project(scope_project.get(row.scope_id), row.project_id):
                    violations.append(
                        Violation(
                            "hub:finding-scope-in-project",
                            f"{noun} {row[0]} of project {row.project_id} names scope {row.scope_id} of "
                            f"{_project_label(scope_project.get(row.scope_id))}",
                        )
                    )
        return violations


class ReviewFindingProjectIsChunks(HubQueryCheck):
    """``hub:review-finding-project-is-chunks`` — a review-sourced finding belongs to the project of the chunk whose
    review raised it. A finding whose raising chunk has no row is not judged here: there is no project to compare."""

    def run(self) -> list[Violation]:
        chunks, findings = hub.chunks, hub.findings
        chunk_project = {
            row.chunk_id: row.project_id for row in self.rows(select(chunks.c.chunk_id, chunks.c.project_id))
        }
        violations: list[Violation] = []
        for row in self.rows(
            select(findings.c.finding_id, findings.c.raised_by_chunk_id, findings.c.project_id)
            .where(findings.c.source == "review", findings.c.raised_by_chunk_id.is_not(None))
            .order_by(findings.c.finding_id)
        ):
            raised_in = chunk_project.get(row.raised_by_chunk_id)
            if raised_in is not None and raised_in != row.project_id:
                violations.append(
                    Violation(
                        "hub:review-finding-project-is-chunks",
                        f"review finding {row.finding_id} of project {row.project_id} was raised by chunk "
                        f"{row.raised_by_chunk_id} of project {raised_in}",
                    )
                )
        return violations


class QuestionProjectIsChunks(HubQueryCheck):
    """``hub:question-project-is-chunks`` — a question belongs to the project of the chunk that asked it."""

    def run(self) -> list[Violation]:
        chunks, questions = hub.chunks, hub.questions
        chunk_project = {
            row.chunk_id: row.project_id for row in self.rows(select(chunks.c.chunk_id, chunks.c.project_id))
        }
        violations: list[Violation] = []
        for row in self.rows(
            select(questions.c.question_id, questions.c.chunk_id, questions.c.project_id).order_by(
                questions.c.tenant_id, questions.c.question_id
            )
        ):
            asked_in = chunk_project.get(row.chunk_id)
            if asked_in is not None and asked_in != row.project_id:
                violations.append(
                    Violation(
                        "hub:question-project-is-chunks",
                        f"question {row.question_id} of project {row.project_id} was asked on chunk "
                        f"{row.chunk_id} of project {asked_in}",
                    )
                )
        return violations


class DecisionProjectIsChunks(HubQueryCheck):
    """``hub:decision-project-is-chunks`` — a decision belongs to the project of the chunk it gates."""

    def run(self) -> list[Violation]:
        chunks, decisions = hub.chunks, hub.decisions
        chunk_project = {
            row.chunk_id: row.project_id for row in self.rows(select(chunks.c.chunk_id, chunks.c.project_id))
        }
        violations: list[Violation] = []
        for row in self.rows(
            select(decisions.c.decision_id, decisions.c.chunk_id, decisions.c.project_id).order_by(
                decisions.c.decision_id
            )
        ):
            gated_in = chunk_project.get(row.chunk_id)
            if gated_in is not None and gated_in != row.project_id:
                violations.append(
                    Violation(
                        "hub:decision-project-is-chunks",
                        f"decision {row.decision_id} of project {row.project_id} gates chunk "
                        f"{row.chunk_id} of project {gated_in}",
                    )
                )
        return violations


class ProposalCitesOwnProject(HubQueryCheck):
    """``hub:proposal-cites-own-project`` — every finding a garden proposal cites belongs to the proposal's own
    project."""

    def run(self) -> list[Violation]:
        proposals, findings, cites = hub.garden_proposals, hub.findings, hub.garden_proposal_findings
        proposal_project = {
            row.proposal_id: row.project_id
            for row in self.rows(select(proposals.c.proposal_id, proposals.c.project_id))
        }
        finding_project = {
            row.finding_id: row.project_id for row in self.rows(select(findings.c.finding_id, findings.c.project_id))
        }
        violations: list[Violation] = []
        for row in self.rows(
            select(cites.c.proposal_id, cites.c.finding_id).order_by(cites.c.proposal_id, cites.c.finding_id)
        ):
            cited_by, cited = proposal_project.get(row.proposal_id), finding_project.get(row.finding_id)
            if not _same_project(cited_by, cited):
                violations.append(
                    Violation(
                        "hub:proposal-cites-own-project",
                        f"proposal {row.proposal_id} of {_project_label(cited_by)} cites finding {row.finding_id} "
                        f"of {_project_label(cited)}",
                    )
                )
        return violations


class SlugNotCurrentAndFormer(HubQueryCheck):
    """``hub:slug-not-current-and-former`` — taking a slug removes its former-slug row in the same write, so within a
    tenant no slug is both some record's current slug and a former one, for every slugged kind."""

    #: Each slugged kind: its noun, record table, former-slug table, and the id column both carry.
    KINDS: ClassVar[tuple[tuple[str, Table, Table, str], ...]] = (
        ("project", hub.projects, hub.project_former_slugs, "project_id"),
        ("repository", hub.repositories, hub.repository_former_slugs, "repository_id"),
        ("work source", hub.work_sources, hub.work_source_former_slugs, "work_source_id"),
        ("forge", hub.forges, hub.forge_former_slugs, "forge_id"),
    )

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        for noun, records, former, id_column in self.KINDS:
            holder = {
                (row.tenant_id, row.slug): row[2]
                for row in self.rows(select(records.c.tenant_id, records.c.slug, records.c[id_column]))
            }
            violations.extend(
                Violation(
                    "hub:slug-not-current-and-former",
                    f"tenant {row.tenant_id}: slug {row.slug} is {noun} {holder[(row.tenant_id, row.slug)]}'s and "
                    f"{noun} {row[2]}'s former slug",
                )
                for row in self.rows(
                    select(former.c.tenant_id, former.c.slug, former.c[id_column]).order_by(
                        former.c.tenant_id, former.c.slug
                    )
                )
                if (row.tenant_id, row.slug) in holder
            )
        return violations


class RepositoryReferencesInTenant(HubQueryCheck):
    """``hub:repository-references-in-tenant`` — every stored reference to a repository names one of the reference's
    own tenant: its lifecycle facts, its links and their facts and landing positions, the landings that name one, the
    terms a submission lands it by, and the change rows keyed by a repository id (a repository's and a project
    link's). The foreign keys say only that the repository exists,
    not whose it is, so only a read can show a row filed under another tenant's repository."""

    #: Each referring table, its key column for a message, and its column naming the repository.
    REFERRING: ClassVar[tuple[tuple[Table, str, str], ...]] = (
        (hub.repository_lifecycle_facts, "id", "repository_id"),
        (hub.project_repository_links, "project_id", "repository_id"),
        (hub.project_repository_link_facts, "id", "repository_id"),
        (hub.project_repository_positions, "id", "repository_id"),
        (hub.delivery_repo_landed, "id", "repository_id"),
        (hub.flow_submission_terms, "submission_id", "repository_id"),
    )

    def run(self) -> list[Violation]:
        repositories = hub.repositories
        tenant_of = {
            row.repository_id: row.tenant_id
            for row in self.rows(select(repositories.c.repository_id, repositories.c.tenant_id))
        }
        violations: list[Violation] = []
        for table, key, column in self.REFERRING:
            query = select(table.c[key], table.c.tenant_id, table.c[column]).where(table.c[column].is_not(None))
            violations.extend(
                self._violation(table.name, row[0], row.tenant_id, row[2])
                for row in self.rows(query.order_by(table.c[key]))
                if tenant_of.get(row[2]) != row.tenant_id
            )
        changes = hub.config_changes
        keyed = select(changes.c.id, changes.c.tenant_id, changes.c.record_key).where(
            changes.c.record_kind.in_(("repository", "project_repository_link"))
        )
        violations.extend(
            self._violation("config_changes", row.id, row.tenant_id, row.record_key)
            for row in self.rows(keyed.order_by(changes.c.id))
            if tenant_of.get(row.record_key) != row.tenant_id
        )
        return violations

    @staticmethod
    def _violation(table: str, key: object, tenant: str, repository_id: str) -> Violation:
        return Violation(
            "hub:repository-references-in-tenant",
            f"{table} row {key} of tenant {tenant} names repository {repository_id}, not a repository of its tenant",
        )


class WorkSourceReferencesInTenant(HubQueryCheck):
    """``hub:work-source-references-in-tenant`` — every stored reference to a work source names one of the
    reference's own tenant: its lifecycle facts, its links and their facts, its forge annotations, the change rows
    keyed by a work-source id (a source's and a project link's), and every work ref's source, which is ``hub`` or a
    source id. The foreign keys say only that the source exists, not whose it is, and a work ref's source has no
    foreign key at all, so a ref left naming no source — one stranded before sources were records — is reported
    here too."""

    #: Each referring table, its key column for a message, and its column naming the work source.
    REFERRING: ClassVar[tuple[tuple[Table, str, str], ...]] = (
        (hub.work_source_lifecycle_facts, "id", "work_source_id"),
        (hub.project_source_links, "project_id", "work_source_id"),
        (hub.project_source_link_facts, "id", "work_source_id"),
        (hub.forge_annotation_facts, "id", "work_source_id"),
        (hub.chunk_work_refs, "id", "source"),
        (hub.work_item_closures, "id", "source"),
        (hub.close_intents, "id", "source"),
        (hub.garden_proposal_closures, "id", "source"),
    )

    def run(self) -> list[Violation]:
        sources = hub.work_sources
        tenant_of = {
            row.work_source_id: row.tenant_id
            for row in self.rows(select(sources.c.work_source_id, sources.c.tenant_id))
        }
        violations: list[Violation] = []
        for table, key, column in self.REFERRING:
            query = select(table.c[key], table.c.tenant_id, table.c[column]).where(
                table.c[column].is_not(None), table.c[column] != RESERVED_HUB_SOURCE_NAME
            )
            violations.extend(
                self._violation(table.name, row[0], row.tenant_id, row[2])
                for row in self.rows(query.order_by(table.c[key]))
                if tenant_of.get(row[2]) != row.tenant_id
            )
        changes = hub.config_changes
        keyed = select(changes.c.id, changes.c.tenant_id, changes.c.record_key).where(
            changes.c.record_kind.in_(("work_source", "project_source_link"))
        )
        violations.extend(
            self._violation("config_changes", row.id, row.tenant_id, row.record_key)
            for row in self.rows(keyed.order_by(changes.c.id))
            if tenant_of.get(row.record_key) != row.tenant_id
        )
        return violations

    @staticmethod
    def _violation(table: str, key: object, tenant: str, work_source_id: str) -> Violation:
        return Violation(
            "hub:work-source-references-in-tenant",
            f"{table} row {key} of tenant {tenant} names work source {work_source_id}, not a work source of its tenant",
        )


def _retired_keys(check: HubQueryCheck, facts: Table, key: str) -> set[str]:
    """Every record whose newest (highest ``id``) lifecycle fact in ``facts`` retires it."""
    newest = select(facts.c[key], func.max(facts.c.id).label("id")).group_by(facts.c[key]).subquery()
    return {
        row[0]
        for row in check.rows(
            select(facts.c[key]).join(newest, facts.c.id == newest.c.id).where(facts.c.retired.is_(True))
        )
    }


class ForgeReferenceStanding(HubQueryCheck):
    """``hub:forge-reference-standing`` — every repository and work source names a forge, no enabled one names a
    retired forge, every work source names a forge whose kind is its provider, and each record's deprecated address
    columns equal its forge's: a repository's ``forge_api_url`` and a work source's ``api_base`` the forge's
    ``api_base``, and a work source's ``web_base`` the forge's own. A reference naming no forge of its tenant is
    ``hub:forge-references-in-tenant``'s to report."""

    def run(self) -> list[Violation]:
        forges = hub.forges
        held = {
            (row.tenant_id, row.forge_id): row
            for row in self.rows(
                select(forges.c.tenant_id, forges.c.forge_id, forges.c.kind, forges.c.api_base, forges.c.web_base)
            )
        }
        retired_forges = _retired_keys(self, hub.forge_lifecycle_facts, "forge_id")
        repositories, sources = hub.repositories, hub.work_sources
        retired_repositories = _retired_keys(self, hub.repository_lifecycle_facts, "repository_id")
        retired_sources = _retired_keys(self, hub.work_source_lifecycle_facts, "work_source_id")
        violations: list[Violation] = []
        for row in self.rows(
            select(
                repositories.c.tenant_id,
                repositories.c.repository_id,
                repositories.c.forge_id,
                repositories.c.forge_api_url,
            ).order_by(repositories.c.repository_id)
        ):
            subject = f"tenant {row.tenant_id}: repository {row.repository_id}"
            if row.forge_id is None:
                violations.append(self._violation(f"{subject} names no forge"))
                continue
            forge = held.get((row.tenant_id, row.forge_id))
            if forge is None:
                continue
            if row.repository_id not in retired_repositories and row.forge_id in retired_forges:
                violations.append(self._violation(f"{subject} is enabled and names retired forge {row.forge_id}"))
            if row.forge_api_url != forge.api_base:
                violations.append(
                    self._violation(
                        f"{subject} holds forge_api_url {row.forge_api_url!r}, not its forge {row.forge_id}'s "
                        f"api_base {forge.api_base!r}"
                    )
                )
        for row in self.rows(
            select(
                sources.c.tenant_id,
                sources.c.work_source_id,
                sources.c.provider,
                sources.c.forge_id,
                sources.c.api_base,
                sources.c.web_base,
            ).order_by(sources.c.work_source_id)
        ):
            subject = f"tenant {row.tenant_id}: work source {row.work_source_id}"
            if row.forge_id is None:
                violations.append(self._violation(f"{subject} names no forge"))
                continue
            forge = held.get((row.tenant_id, row.forge_id))
            if forge is None:
                continue
            if row.work_source_id not in retired_sources and row.forge_id in retired_forges:
                violations.append(self._violation(f"{subject} is enabled and names retired forge {row.forge_id}"))
            if forge.kind != row.provider:
                violations.append(
                    self._violation(
                        f"{subject} of provider {row.provider} names forge {row.forge_id} of kind {forge.kind}"
                    )
                )
            for column, value, expected in (
                ("api_base", row.api_base, forge.api_base),
                ("web_base", row.web_base, forge.web_base),
            ):
                if value != expected:
                    violations.append(
                        self._violation(
                            f"{subject} holds {column} {value!r}, not its forge {row.forge_id}'s {expected!r}"
                        )
                    )
        return violations

    @staticmethod
    def _violation(detail: str) -> Violation:
        return Violation("hub:forge-reference-standing", detail)


class ForgeReferencesInTenant(HubQueryCheck):
    """``hub:forge-references-in-tenant`` — every stored reference to a forge names a forge that exists, of the
    reference's own tenant: the repositories' and work sources' ``forge_id``, the forge's former slugs and lifecycle
    facts, and the change rows keyed by a forge id. Through the forge transition the two ``forge_id`` columns carry no
    foreign key, so this read stands in for one."""

    #: Each referring table, its key column for a message, and its column naming the forge.
    REFERRING: ClassVar[tuple[tuple[Table, str, str], ...]] = (
        (hub.repositories, "repository_id", "forge_id"),
        (hub.work_sources, "work_source_id", "forge_id"),
        (hub.forge_former_slugs, "slug", "forge_id"),
        (hub.forge_lifecycle_facts, "id", "forge_id"),
    )

    def run(self) -> list[Violation]:
        forges = hub.forges
        tenant_of = {row.forge_id: row.tenant_id for row in self.rows(select(forges.c.forge_id, forges.c.tenant_id))}
        violations: list[Violation] = []
        for table, key, column in self.REFERRING:
            query = select(table.c[key], table.c.tenant_id, table.c[column]).where(table.c[column].is_not(None))
            violations.extend(
                self._violation(table.name, row[0], row.tenant_id, row[2])
                for row in self.rows(query.order_by(table.c.tenant_id, table.c[key]))
                if tenant_of.get(row[2]) != row.tenant_id
            )
        changes = hub.config_changes
        keyed = select(changes.c.id, changes.c.tenant_id, changes.c.record_key).where(changes.c.record_kind == "forge")
        violations.extend(
            self._violation("config_changes", row.id, row.tenant_id, row.record_key)
            for row in self.rows(keyed.order_by(changes.c.id))
            if tenant_of.get(row.record_key) != row.tenant_id
        )
        return violations

    @staticmethod
    def _violation(table: str, key: object, tenant: str, forge_id: str) -> Violation:
        return Violation(
            "hub:forge-references-in-tenant",
            f"{table} row {key} of tenant {tenant} names forge {forge_id}, not a forge of its tenant",
        )


def _earliest_links(check: HubQueryCheck, facts: Table, name: str) -> dict[tuple[str, str], datetime]:
    """Each (project, name) a link fact ever linked, to its earliest linking instant: a link linked by an instant
    is one with a ``linked`` fact set at or before it, whatever later facts say."""
    earliest: dict[tuple[str, str], datetime] = {}
    for row in check.rows(select(facts.c.project_id, facts.c[name], facts.c.set_at).where(facts.c.linked.is_(True))):
        key = (row.project_id, row[1])
        if key not in earliest or row.set_at < earliest[key]:
            earliest[key] = row.set_at
    return earliest


class ChunkSourcesLinked(HubQueryCheck):
    """``hub:chunk-sources-linked`` — every source a chunk's work refs name is linked to the chunk's project, as the
    link's facts answer. The originating ref (a chunk's first) was linked when the chunk was minted, since ingest
    chose the project by that link; a ref folded in later carries no instant of its own, so it is judged only as
    linked at some time. The built-in hub source is linked to every project without a stored link. A chunk minted
    before its project was created was minted before any link existed and is not judged."""

    def run(self) -> list[Violation]:
        projects, chunks, refs = hub.projects, hub.chunks, hub.chunk_work_refs
        created = {
            row.project_id: row.created_at for row in self.rows(select(projects.c.project_id, projects.c.created_at))
        }
        minted = {
            row.chunk_id: (row.project_id, row.minted_at)
            for row in self.rows(select(chunks.c.chunk_id, chunks.c.project_id, chunks.c.minted_at))
        }
        linked = _earliest_links(self, hub.project_source_link_facts, "work_source_id")
        violations: list[Violation] = []
        judged: set[str] = set()
        for row in self.rows(select(refs.c.chunk_id, refs.c.source, refs.c.ref).order_by(refs.c.chunk_id, refs.c.id)):
            originating = row.chunk_id not in judged
            judged.add(row.chunk_id)
            if row.source == RESERVED_HUB_SOURCE_NAME or row.chunk_id not in minted:
                continue
            project_id, minted_at = minted[row.chunk_id]
            if project_id not in created or minted_at < created[project_id]:
                continue
            since = linked.get((project_id, row.source))
            if since is None:
                violations.append(
                    Violation(
                        "hub:chunk-sources-linked",
                        f"chunk {row.chunk_id} of project {project_id} names {row.source}:{row.ref}, "
                        "a source its project never linked",
                    )
                )
            elif originating and since > minted_at:
                violations.append(
                    Violation(
                        "hub:chunk-sources-linked",
                        f"chunk {row.chunk_id} of project {project_id} was minted from {row.source}:{row.ref}, "
                        "a source its project had not linked by then",
                    )
                )
        return violations


class LandedRepositoryLinked(HubQueryCheck):
    """``hub:landed-repository-linked`` — every per-repository landing that names its repository record names one
    the chunk's project had linked when the landing was written, as the link's facts answer. A link unlinked while
    the landing step ran still answers: the step resolved its repositories while linked. A landing naming no
    repository was written before the record was recorded and is not judged."""

    def run(self) -> list[Violation]:
        chunks, landed = hub.chunks, hub.delivery_repo_landed
        project_of = {row.chunk_id: row.project_id for row in self.rows(select(chunks.c.chunk_id, chunks.c.project_id))}
        linked = _earliest_links(self, hub.project_repository_link_facts, "repository_id")
        violations: list[Violation] = []
        for row in self.rows(
            select(landed.c.id, landed.c.chunk_id, landed.c.repo, landed.c.repository_id, landed.c.landed_at)
            .where(landed.c.repository_id.is_not(None))
            .order_by(landed.c.id)
        ):
            project_id = project_of.get(row.chunk_id)
            since = linked.get((project_id, row.repository_id)) if project_id is not None else None
            if since is None or since > row.landed_at:
                violations.append(
                    Violation(
                        "hub:landed-repository-linked",
                        f"chunk {row.chunk_id} of {_project_label(project_id)} landed {row.repo} in repository "
                        f"{row.repository_id}, which its project had not linked by then",
                    )
                )
        return violations


class LandingPositionOnLink(HubQueryCheck):
    """``hub:landing-position-on-link`` — every landing position names a link of its own tenant. The foreign key
    says only that the link exists, not whose it is, so only a read can show a position filed under another
    tenant's link. An unlinked link's positions stand: relinking brings them back."""

    def run(self) -> list[Violation]:
        links, positions = hub.project_repository_links, hub.project_repository_positions
        tenant_of = {
            (row.project_id, row.repository_id): row.tenant_id
            for row in self.rows(select(links.c.project_id, links.c.repository_id, links.c.tenant_id))
        }
        return [
            Violation(
                "hub:landing-position-on-link",
                f"project_repository_positions row {row.id} of tenant {row.tenant_id} names the link of project "
                f"{row.project_id} to repository {row.repository_id}, not a link of its tenant",
            )
            for row in self.rows(
                select(
                    positions.c.id, positions.c.tenant_id, positions.c.project_id, positions.c.repository_id
                ).order_by(positions.c.id)
            )
            if tenant_of.get((row.project_id, row.repository_id)) != row.tenant_id
        ]


class SubmissionTermsStanding(HubQueryCheck):
    """``hub:submission-terms-standing`` — every submission's terms name a repository the submission holds a flow
    for, and a submission with an open turn has terms for every repository it holds. Open rather than started: a
    submission in flight when the terms arrived has turns from before them and takes its terms at its next turn."""

    def run(self) -> list[Violation]:
        held, flows, terms, turns = hub.flow_submission_flows, hub.flows, hub.flow_submission_terms, hub.hub_turns
        holds: dict[str, set[str]] = {}
        for row in self.rows(
            select(held.c.submission_id, flows.c.repository_id)
            .join(flows, flows.c.flow_id == held.c.flow_id)
            .order_by(held.c.submission_id, flows.c.repository_id)
        ):
            holds.setdefault(row.submission_id, set()).add(row.repository_id)
        taken: dict[str, set[str]] = {}
        violations: list[Violation] = []
        for row in self.rows(
            select(terms.c.submission_id, terms.c.repository_id).order_by(terms.c.submission_id, terms.c.repository_id)
        ):
            taken.setdefault(row.submission_id, set()).add(row.repository_id)
            if row.repository_id not in holds.get(row.submission_id, set()):
                violations.append(
                    Violation(
                        "hub:submission-terms-standing",
                        f"submission {row.submission_id} has terms for repository {row.repository_id}, "
                        "which it holds no flow for",
                    )
                )
        open_turns = select(turns.c.submission_id).where(turns.c.ended_at.is_(None)).distinct()
        for submission_id in sorted(self.rows(open_turns).scalars()):
            violations.extend(
                Violation(
                    "hub:submission-terms-standing",
                    f"submission {submission_id} has an open turn but no terms for repository {repository_id}",
                )
                for repository_id in sorted(holds.get(submission_id, set()) - taken.get(submission_id, set()))
            )
        return violations


@dataclass(frozen=True)
class TenantReads:
    """One tenant's read repositories, the facts checks' view of its chunks."""

    facts: IReadChunkFactsRepository
    record: IReadChunkRecordRepository
    flows: IReadFlows


#: Builds one tenant's :class:`TenantReads` over the scoped connections it is handed.
TenantReadsOpener = Callable[[ScopedConnections], TenantReads]


@dataclass(frozen=True)
class HubInvariants:
    """The hub store's durable invariants (transitions, epochs, delivery). ``tenant_reads`` builds each open
    tenant's own read adapters for the checks that load a chunk's facts. :meth:`run` reads everything inside one read
    transaction, so every check sees the store as of one moment."""

    connections: HubStoreConnections
    tenant_reads: TenantReadsOpener
    clock: IClock

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        with self.connections.read_transaction("check hub invariants") as borrowed:
            # The query checks audit every open tenant's rows at once; NoRowOfADeletedTenant alone reads a closed one.
            with _hub_wide(borrowed) as conn:
                checks: tuple[QueryCheck, ...] = (
                    OneTransitionPerNodeEpoch(conn),
                    EpochConsistentTransitions(conn),
                    EpochsOwned(conn),
                    RouteSeqUnique(conn),
                    OneLiveRoutePerChunk(conn),
                    PerRepoLandIdempotent(conn),
                    PerRepoMarkerIdempotent(conn),
                    NoDoubleDelivery(conn),
                    OneTurnPerFlow(conn),
                    OneTurnPerVisit(conn),
                    HubNodeHasAction(conn),
                    NoDoubleTerminalClosure(conn),
                    NoPendingIntentAgainstATerminalRef(conn),
                    NoUnenqueuedClosableRef(conn),
                    SegmentChunkResolves(conn),
                    NoStandingDependencyCycle(conn),
                    NoDuplicateStandingDependency(conn),
                    NoStandingDependencyOntoEphemeralChunk(conn),
                    ChunkProjectInTenant(conn),
                    RoutineScopeSameProject(conn),
                    FindingScopeInProject(conn),
                    ReviewFindingProjectIsChunks(conn),
                    QuestionProjectIsChunks(conn),
                    DecisionProjectIsChunks(conn),
                    ProposalCitesOwnProject(conn),
                    WorkItemProjectInTenant(conn),
                    SlugNotCurrentAndFormer(conn),
                    RepositoryReferencesInTenant(conn),
                    WorkSourceReferencesInTenant(conn),
                    ForgeReferenceStanding(conn),
                    ForgeReferencesInTenant(conn),
                    ChunkSourcesLinked(conn),
                    LandedRepositoryLinked(conn),
                    LandingPositionOnLink(conn),
                    SubmissionTermsStanding(conn),
                )
                for check in checks:
                    violations.extend(check.run())
                violations.extend(MigrationsAtomic(conn).run())
                violations.extend(NoRowOfADeletedTenant(conn).run())
                open_tenants = _open_tenants(conn)
            for tenant in open_tenants:
                # The facts checks load chunks through the tenant's own adapters, one tenant at a time.
                reads = self.tenant_reads(borrowed.scoped(StoreScope(tenant)))
                for facts_check in (
                    DerivationAndDelivery(reads.facts, reads.record),
                    LiveRouteHasToken(reads.facts, reads.record),
                ):
                    violations.extend(
                        Violation(found.invariant, f"tenant {tenant}: {found.detail}") for found in facts_check.run()
                    )
                line = FlowLineStanding(reads.flows, reads.facts, self.clock)
                violations.extend(
                    Violation(found.invariant, f"tenant {tenant}: {found.detail}") for found in line.run()
                )
        return violations


@contextmanager
def _hub_wide(connections: HubStoreConnections) -> Generator[Connection, None, None]:
    """The read connection marked hub-scoped by this audit itself, for as long as the ``with`` lasts: every check on
    it is a ``SELECT`` over all tenants' rows. The mark is set in place on the connection, so it is cleared on leaving,
    before the tenants' own stores read through that same connection."""
    with connections.read("check hub invariants") as conn:
        conn.execution_options(**{HUB_SCOPED_OPTION: True})
        try:
            yield conn
        finally:
            conn.execution_options(**{HUB_SCOPED_OPTION: False})


def _open_tenants(conn: Connection) -> list[TenantId]:
    """Every tenant no deletion has closed, in id order. A closed tenant's rows are being drained, a chunk's
    facts before its record, so its chunks are no longer whole and are left out of the facts checks, as its rows are
    out of every :class:`HubQueryCheck`."""
    closed = select(hub.tenant_deletions.c.tenant_id)
    tenants = hub.tenants.c.tenant_id
    return [
        TenantId(t) for t in conn.execute(select(tenants).where(tenants.not_in(closed)).order_by(tenants)).scalars()
    ]
