"""The facts-level invariant checker (``bzh:invariant-checker``).

After any crash → restart → recover cycle, the durable facts in both stores must still
satisfy the correctness conditions the design rests on. A violation names the exact
broken invariant, so a failure points at the window and the rule. Because both stores
are facts-only (``bzh:facts-not-status``), every check is a plain query; nothing mutates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from sqlalchemy import Connection, CursorResult, Engine, Select, Table, and_, func, or_, select

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.migration_source import MigrationSource
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.roles import collaborator, domain_model
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.model import ChunkFacts, RouteHistory
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunk.ports.record import IReadChunkRecordRepository
from blizzard.hub.domain.graph.model import RESERVED_TERMINAL
from blizzard.hub.domain.kernel.hub_source import RESERVED_HUB_SOURCE_NAME
from blizzard.hub.store import schema as hub
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_rows import DEFAULT_MODEL
from blizzard.hub.store.scoped import TENANT_COLUMN, is_tenant_owned
from blizzard.hub.tenancy import HUB_SCOPED_OPTION, StoreScope, TenantId
from blizzard.runner.composition import build_host_stores
from blizzard.runner.process.internal.linux_process_probe import LinuxProcessProbe
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.store import schema as runner
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.scoped import RUNNER_COLUMN, RUNNER_SCOPING
from blizzard.runner.transcripts.invocation_boundaries import WORKER_STARTING_KINDS

# The ``lease_facts.runner_id`` a hub mint records — its own lease row, never a runner's.
_HUB_LEASE_RUNNER_ID = "hub"


@domain_model
@dataclass(frozen=True)
class Violation:
    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.invariant}] {self.detail}"


@collaborator
@dataclass(frozen=True)
class Check:
    """One durable invariant, asserted against a store — every breach it finds, in the
    order it found them."""

    def run(self) -> list[Violation]:
        raise NotImplementedError


@dataclass(frozen=True)
class QueryCheck(Check):
    """An invariant answerable by plain queries over one open read connection."""

    conn: Connection


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


class OneLiveLeasePerChunk(QueryCheck):
    """A live lease is one with no closure fact (``bzh:facts-not-status``); at most one per
    chunk (MAX_AGENTS math)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        closed = select(runner.lease_closures.c.lease_id)
        live = select(runner.leases.c.chunk_id).where(runner.leases.c.lease_id.notin_(closed))
        per_chunk = Counter(row[0] for row in self.conn.execute(live))
        for chunk_id, n in per_chunk.items():
            if n > 1:
                violations.append(Violation("runner:one-live-lease-per-chunk", f"chunk {chunk_id} has {n} live leases"))
        return violations


class NoUnownedLiveLeaseProcess(QueryCheck):
    """Once a lease closes, no generation it launched is left ambiguously provisional —
    a pid with neither an identified session nor a recorded identity failure.
    Only CLOSED leases are checked: REAP always resolves a provisional generation, killing
    its group, before ``Attempt.fail`` closes the lease, so an ambiguous one means a leak."""

    def run(self) -> list[Violation]:
        closed = select(runner.lease_closures.c.lease_id)
        stmt = (
            select(runner.lease_spawns.c.lease_id, runner.lease_spawns.c.id)
            .where(runner.lease_spawns.c.lease_id.in_(closed))
            .where(runner.lease_spawns.c.pid.is_not(None))
            .where(runner.lease_spawns.c.session_id.is_(None))
            .where(runner.lease_spawns.c.identity_failed_at.is_(None))
        )
        return [
            Violation(
                "runner:no-unowned-live-lease-process",
                f"closed lease {row.lease_id} generation {row.id} was never identified or marked failed",
            )
            for row in self.conn.execute(stmt)
        ]


@dataclass(frozen=True)
class ActiveLeaseProcessIsLive(QueryCheck):
    """:class:`NoUnownedLiveLeaseProcess`'s ACTIVE-lease counterpart: an OPEN
    lease's still-provisional generation, probed against a real OS process — a recorded
    pid with nothing running there is the crash-recovery gap RESUME exists to close. Also
    flags two ACTIVE leases recording the same (pid, start_time), liveness unprobed."""

    process: IProcessProbe

    def run(self) -> list[Violation]:
        closed = select(runner.lease_closures.c.lease_id)
        active = runner.leases.c.lease_id.notin_(closed)
        violations: list[Violation] = []
        # Bounded to the newest open generation per lease (MAX/GROUP BY, `bzh:sql-portable`),
        # mirroring `lease_liveness_store._open_provisional_spawn_id`'s own predicate.
        newest_open_provisional = (
            select(runner.lease_spawns.c.lease_id, func.max(runner.lease_spawns.c.id).label("spawn_id"))
            .where(runner.lease_spawns.c.session_id.is_(None))
            .where(runner.lease_spawns.c.pid.is_not(None))
            .group_by(runner.lease_spawns.c.lease_id)
            .subquery()
        )
        provisional_stmt = (
            select(
                runner.leases.c.lease_id,
                runner.leases.c.pid,
                runner.leases.c.process_start_time,
                runner.lease_spawns.c.identity_failed_at,
            )
            .select_from(
                runner.leases.join(
                    newest_open_provisional,
                    newest_open_provisional.c.lease_id == runner.leases.c.lease_id,
                    isouter=True,  # a hand-seeded `leases` row with no `lease_spawns` row at all
                    # still reads as "not recorded failed" — never silently exempted (bzh:sql-portable).
                ).join(
                    runner.lease_spawns,
                    runner.lease_spawns.c.id == newest_open_provisional.c.spawn_id,
                    isouter=True,
                )
            )
            .where(active)
            .where(runner.leases.c.pid.is_not(None))
            .where(runner.leases.c.session_id.is_(None))
        )
        for row in self.conn.execute(provisional_stmt):
            if row.identity_failed_at is not None:
                continue  # known, pending-reconciliation state — REAP hasn't closed it yet
            pid = int(row.pid)
            start = str(row.process_start_time or "")
            if not self.process.is_alive(pid, start):
                violations.append(
                    Violation(
                        "runner:active-lease-process-is-live",
                        f"active lease {row.lease_id} has a provisional generation recorded at pid "
                        f"{pid}, but no live process exists there",
                    )
                )
        owner_stmt = (
            select(runner.leases.c.lease_id, runner.leases.c.pid, runner.leases.c.process_start_time)
            .where(active)
            .where(runner.leases.c.pid.is_not(None))
        )
        owner_of: dict[tuple[int, str], str] = {}
        for row in self.conn.execute(owner_stmt):
            pid = int(row.pid)
            start = str(row.process_start_time or "")
            key = (pid, start)
            prior_owner = owner_of.get(key)
            if prior_owner is not None:
                # Liveness is never probed here — the shared record itself is the violation.
                violations.append(
                    Violation(
                        "runner:active-lease-process-is-live",
                        f"active leases {prior_owner} and {row.lease_id} both record the same "
                        f"process (pid={pid}) as their own — an unowned/ambiguous claim",
                    )
                )
            else:
                owner_of[key] = row.lease_id
        return violations


class UniqueEnvBinding(QueryCheck):
    """A held environment of a workspace (a binding with no release fact) is bound to at most one chunk across the
    host: two chunks sharing a held ``(workspace_id, environment_id)`` would double-book it. A held row naming no
    workspace may be in any pool, so it conflicts with every other chunk's held row on the same environment id."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        by_env: dict[str, dict[str | None, set[str]]] = {}
        for chunk_id, env_id, workspace_id in self._held_bindings():
            by_env.setdefault(env_id, {}).setdefault(workspace_id, set()).add(chunk_id)
        for env_id, by_workspace in by_env.items():
            unknown = by_workspace.get(None, set())
            for workspace_id, chunks in sorted(by_workspace.items(), key=lambda item: item[0] or ""):
                if workspace_id is None:
                    continue
                if len(chunks) > 1:
                    violations.append(
                        Violation(
                            "runner:unique-env-binding",
                            f"env {env_id} of workspace {workspace_id} held by chunks {sorted(chunks)}",
                        )
                    )
            every = set().union(*by_workspace.values())
            if unknown and len(every) > 1:
                violations.append(
                    Violation(
                        "runner:unique-env-binding",
                        f"env {env_id} held by chunks {sorted(every)}, at least one naming no workspace",
                    )
                )
        return violations

    def _held_bindings(self) -> list[tuple[str, str, str | None]]:
        bindings, releases = runner.env_bindings, runner.binding_releases
        released = (
            select(releases.c.id)
            .where(
                releases.c.chunk_id == bindings.c.chunk_id,
                releases.c.environment_id == bindings.c.environment_id,
                releases.c.runner_id == bindings.c.runner_id,
                releases.c.released_at >= bindings.c.bound_at,
            )
            .exists()
        )
        return [
            (chunk_id, env_id, workspace_id)
            for chunk_id, env_id, workspace_id in self.conn.execute(
                select(bindings.c.chunk_id, bindings.c.environment_id, bindings.c.workspace_id).where(~released)
            )
        ]


class RowsNameRegisteredRunner(QueryCheck):
    """Every runner-scoped row names a runner the store's ``runner_identity`` records. A row the bound runner's scoped
    reads cannot see is lost to it: a lease never reaped, a binding never released, an outbound fact never flushed.
    ``runner_identity`` keys itself by its own id, so it is not judged; nor is ``outbox_counters``, which outlives a
    runner reset out of the store by design, so that runner never issues a ``seq`` at or below its hub's mark again."""

    _TABLES: ClassVar[tuple[Table, ...]] = tuple(
        table
        for table in runner.metadata.sorted_tables
        if RUNNER_SCOPING.is_keyed(table) and table not in (runner.runner_identity, runner.outbox_counters)
    )

    def run(self) -> list[Violation]:
        registered = select(runner.runner_identity.c.runner_id)
        violations: list[Violation] = []
        for table in self._TABLES:
            key = table.c[RUNNER_COLUMN]
            for row in self.conn.execute(
                select(key, func.count()).where(key.not_in(registered)).group_by(key).order_by(key)
            ):
                violations.append(
                    Violation(
                        "runner:rows-name-registered-runner",
                        f"{row[1]} {table.name} row(s) name unregistered runner {row[0]}",
                    )
                )
        return violations


def _holes_by_runner(conn: Connection, table: Table, *, pending_only: bool = False) -> dict[str, list[int]]:
    """Each runner's missing seqs among the ``table`` rows it retains from its own lowest pending seq upward, or among
    its pending rows alone (``pending_only``), in runner order. A runner whose sequence has no hole is absent."""
    runner_id_column = table.c[RUNNER_COLUMN]
    if pending_only:
        query = select(runner_id_column, table.c.seq).where(table.c.acked_at.is_(None))
    else:
        floor = (
            select(runner_id_column.label("runner_id"), func.min(table.c.seq).label("floor"))
            .where(table.c.acked_at.is_(None))
            .group_by(runner_id_column)
            .subquery()
        )
        query = (
            select(runner_id_column, table.c.seq)
            .outerjoin(floor, floor.c.runner_id == runner_id_column)
            .where(or_(floor.c.floor.is_(None), table.c.seq >= floor.c.floor))
        )
    by_runner: dict[str, list[int]] = {}
    for runner_id, seq in conn.execute(query.order_by(runner_id_column, table.c.seq)):
        by_runner.setdefault(runner_id, []).append(seq)
    holes: dict[str, list[int]] = {}
    for runner_id, seqs in by_runner.items():
        missing = sorted(set(range(seqs[0], seqs[-1] + 1)) - set(seqs))
        if missing:
            holes[runner_id] = missing
    return holes


class GaplessOutboundSeq(QueryCheck):
    """A hole in a runner's retained outbound seqs would break FIFO idempotent replay — scoped, per runner, to its
    pending floor upward, not every seq it ever minted, since retention (`IWriteOutboundRepository.prune_outbound`)
    prunes below it. Mirrors `GaplessTranscriptOutboundSeq`'s own scoping for its lane."""

    def run(self) -> list[Violation]:
        return [
            Violation(
                "runner:gapless-outbound-seq",
                f"runner {runner_id}: retained outbound seqs not gapless; missing {missing}",
            )
            for runner_id, missing in _holes_by_runner(self.conn, runner.outbound_buffer).items()
        ]


class GaplessTranscriptOutboundSeq(QueryCheck):
    """A hole in a runner's transcript lane's own *pending* (unacked) seqs means a lost record — scoped, per runner, to
    the pending window, not every seq it ever minted, since an acked non-final row is pruned outright."""

    def run(self) -> list[Violation]:
        return [
            Violation(
                "runner:gapless-transcript-outbound-seq",
                f"runner {runner_id}: pending transcript outbound seqs not gapless; missing {missing}",
            )
            for runner_id, missing in _holes_by_runner(
                self.conn, runner.transcript_outbound_buffer, pending_only=True
            ).items()
        ]


class OutboxCounterAboveSeq(QueryCheck):
    """A runner's outbox counter is at or above every seq its lane retains. A counter below one would issue a seq a
    consumer already holds, or one the hub already treated as final; a lane with rows and no counter is the same
    breach. Judged per runner and lane."""

    _LANES: ClassVar[tuple[tuple[str, Table], ...]] = (
        (runner.OUTBOX_LANE_OUTBOUND, runner.outbound_buffer),
        (runner.OUTBOX_LANE_TRANSCRIPT, runner.transcript_outbound_buffer),
    )

    def run(self) -> list[Violation]:
        counters = {
            (row.runner_id, row.lane): row.last_seq
            for row in self.conn.execute(
                select(
                    runner.outbox_counters.c.runner_id, runner.outbox_counters.c.lane, runner.outbox_counters.c.last_seq
                )
            )
        }
        violations: list[Violation] = []
        for lane, table in self._LANES:
            for runner_id, newest in self.conn.execute(
                select(table.c[RUNNER_COLUMN], func.max(table.c.seq))
                .group_by(table.c[RUNNER_COLUMN])
                .order_by(table.c[RUNNER_COLUMN])
            ):
                counter = counters.get((runner_id, lane))
                if counter is None or counter < newest:
                    held = "no counter" if counter is None else f"counter {counter}"
                    violations.append(
                        Violation(
                            "runner:outbox-counter-above-seq",
                            f"runner {runner_id}: {lane} lane retains seq {newest} but has {held}",
                        )
                    )
        return violations


class TranscriptSegmentFinalizedExactlyOnce(QueryCheck):
    """A finalized segment (`finalized_at` set) has exactly one `final` row buffered for it —
    a step's segments are final by step close, landed once, never zero and never duplicated.
    Unconditional: every segment has a `normalizer_version` to declare, so this
    holds regardless of `[transcripts] ship` or whether a pump ever ran."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        finalized = {
            row[0]
            for row in self.conn.execute(
                select(runner.transcript_segments.c.segment_id).where(
                    runner.transcript_segments.c.finalized_at.is_not(None)
                )
            )
        }
        markers = Counter(
            row[0]
            for row in self.conn.execute(
                select(runner.transcript_outbound_buffer.c.segment_id).where(
                    runner.transcript_outbound_buffer.c.final.is_(True)
                )
            )
        )
        for segment_id in finalized:
            n = markers.get(segment_id, 0)
            if n != 1:
                violations.append(
                    Violation(
                        "runner:transcript-segment-finalized-exactly-once",
                        f"segment {segment_id} is finalized but has {n} final markers buffered",
                    )
                )
        return violations


class OneOpenPauseParkPerLease(QueryCheck):
    """An *open* pause-park is a park fact with no pause-resume at or after it;
    a re-pause is legal."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        open_parks = Counter(lease_id for lease_id, _ in self._open_pause_parks())
        for lease_id, n in open_parks.items():
            if n > 1:
                violations.append(
                    Violation("runner:one-open-pause-park-per-lease", f"lease {lease_id} has {n} open pause-parks")
                )
        return violations

    def _open_pause_parks(self) -> list[tuple[str, datetime]]:
        """The plain-query mirror of the store adapter's ``open_pause_park`` — same ``>=``
        (a same-instant resume is a resume), same per-lease correlation, and the same
        exclusion of a closed lease's park."""
        closed = {row[0] for row in self.conn.execute(select(runner.lease_closures.c.lease_id))}
        resumes: dict[str, list[datetime]] = {}
        for lease_id, resumed_at in self.conn.execute(
            select(runner.pause_park_resumes.c.lease_id, runner.pause_park_resumes.c.resumed_at)
        ):
            resumes.setdefault(lease_id, []).append(resumed_at)
        return [
            (lease_id, parked_at)
            for lease_id, parked_at in self.conn.execute(
                select(runner.pause_parks.c.lease_id, runner.pause_parks.c.parked_at)
            )
            if lease_id not in closed and not any(r >= parked_at for r in resumes.get(lease_id, ()))
        ]


class UsageAttributedOnce(QueryCheck):
    """A harness invocation's usage is attributed once per (lease, generation, kind) (epic #57)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        rows = select(runner.usage_facts.c.lease_id, runner.usage_facts.c.generation, runner.usage_facts.c.kind)
        usage_key = Counter((row[0], row[1], row[2]) for row in self.conn.execute(rows))
        for (lease_id, generation, kind), n in usage_key.items():
            if n > 1:
                violations.append(
                    Violation(
                        "runner:usage-attributed-once",
                        f"lease {lease_id} generation {generation} kind {kind} has {n} usage facts",
                    )
                )
        return violations


class InvocationBoundaryClosedWhenLeaseClosed(QueryCheck):
    """Every invocation boundary a closed lease ever opened is itself closed
    (``bzh:open-facts-declare-closure``) — no lease_id may own a still-open boundary
    once its own closure fact is durable; `Attempt.close` is the one funnel every closure
    path shares, so a hub-terminal chunk closes its boundaries the same as any other."""

    def run(self) -> list[Violation]:
        closed_leases = {row[0] for row in self.conn.execute(select(runner.lease_closures.c.lease_id))}
        stmt = select(runner.invocation_boundaries.c.lease_id, runner.invocation_boundaries.c.kind).where(
            and_(
                runner.invocation_boundaries.c.lease_id.in_(closed_leases),
                runner.invocation_boundaries.c.closed_at.is_(None),
            )
        )
        return [
            Violation(
                "runner:invocation-boundary-closed-when-lease-closed",
                f"lease {row.lease_id} is closed but its {row.kind} invocation boundary is still open",
            )
            for row in self.conn.execute(stmt)
        ]


class WorkerBoundaryKindExclusivePerGeneration(QueryCheck):
    """At most one worker-starting boundary — ``spawn``, ``resume``, or ``nudge`` — exists per
    (lease, generation): the exclusivity ``UsageRecorder._worker_boundary``'s
    try-each-kind lookup depends on. ``judge`` is excluded — it can coexist with one of the
    other three at the same generation by design."""

    def run(self) -> list[Violation]:
        stmt = select(runner.invocation_boundaries.c.lease_id, runner.invocation_boundaries.c.generation).where(
            runner.invocation_boundaries.c.kind.in_(WORKER_STARTING_KINDS)
        )
        key_count = Counter((row[0], row[1]) for row in self.conn.execute(stmt))
        return [
            Violation(
                "runner:worker-boundary-kind-exclusive-per-generation",
                f"lease {lease_id} generation {generation} has {n} worker-starting invocation boundaries",
            )
            for (lease_id, generation), n in key_count.items()
            if n > 1
        ]


class NudgeAtMostOnce(QueryCheck):
    """A lease's ``produces``-unmet nudge fires at most once per (lease, epoch)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        nudge_key = Counter(
            (row[0], row[1])
            for row in self.conn.execute(select(runner.nudge_facts.c.lease_id, runner.nudge_facts.c.epoch))
        )
        for (lease_id, epoch), n in nudge_key.items():
            if n > 1:
                violations.append(
                    Violation("runner:nudge-at-most-once", f"lease {lease_id} epoch {epoch} has {n} nudge facts")
                )
        return violations


class ChecksRecordedWhenMarked(QueryCheck):
    """A ``checks_ran`` marker implies its check result rows exist."""

    def run(self) -> list[Violation]:
        marked = {
            (row[0], row[1])
            for row in self.conn.execute(select(runner.checks_ran.c.lease_id, runner.checks_ran.c.epoch))
        }
        have_rows = {
            (row[0], row[1])
            for row in self.conn.execute(select(runner.check_results.c.lease_id, runner.check_results.c.epoch))
        }
        return [
            Violation(
                "runner:checks-recorded-when-marked",
                f"lease {lease_id} epoch {epoch} is marked checks-ran but has no check_results rows",
            )
            for lease_id, epoch in sorted(marked - have_rows)
        ]


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
    """At most one terminal (``closed``/``gone``) ``work_item_closures`` outcome per
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
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone"]))
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
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone"]))
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
    row for every still-open ``chunk_work_refs`` ref — the nine
    call-site-guarded ``_enqueue_close_intents`` invocations' own derived invariant. A terminal
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
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone"]))
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


class OneLiveExecSlot(HubQueryCheck):
    """At most one hub_exec_slot row per tenant is live (``released_at IS NULL``) at a time
    (pinned by tests/test_pin_foundation.py). Each tenant holds its own slot, so two
    tenants' live slots at once are legal."""

    def run(self) -> list[Violation]:
        slot = hub.hub_exec_slot
        live = func.count()
        crowded = self.rows(
            select(slot.c.tenant_id, live)
            .where(slot.c.released_at.is_(None))
            .group_by(slot.c.tenant_id)
            .having(live > 1)
            .order_by(slot.c.tenant_id)
        ).all()
        return [
            Violation(
                "hub:one-live-exec-slot", f"tenant {row.tenant_id}: {row[1]} hub-execution slots are live at once"
            )
            for row in crowded
        ]


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

        newest: dict[str, object] = {}
        for m in self.rows(select(hub.chunk_migrations)):
            cur = newest.get(m.chunk_id)
            if cur is None or (m.recorded_at, m.epoch) >= (cur.recorded_at, cur.epoch):  # type: ignore[attr-defined]
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
            if chunk.graph_id != m.to_graph_id:  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} pinned {chunk.graph_id} but its newest migration targets {m.to_graph_id}",  # type: ignore[attr-defined]
                    )
                )
            # **Membership**, not equality against `[model_after]` — the list may
            # legitimately grow afterwards (pinned by tests/test_invariant_checker.py).
            elif m.model_after is not None and m.model_after not in DEFAULT_MODEL.decode(chunk.default_model):  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} default_model {chunk.default_model!r} does not carry "  # type: ignore[attr-defined]
                        f"{m.model_after}, which its newest migration re-pinned",  # type: ignore[attr-defined]
                    )
                )
            # A hub landing and an operator restart's own re-pin (#371) both retain the
            # route by design — neither is a torn write, so neither owes a release.
            keeps_route = (
                landed_executor.get(m.landed_node_id) == Executor.HUB  # type: ignore[attr-defined]
                or m.source == MigrationSource.RESTART.value  # type: ignore[attr-defined]
            )
            released = latest_release.get(chunk_id)
            if not keeps_route and (released is None or released < m.recorded_at):  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-route-released",
                        f"chunk {chunk_id} migrated at {m.recorded_at} but no route release landed "  # type: ignore[attr-defined]
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
class RunnerInvariants:
    """The runner store's durable invariants (leases, bindings, outbound buffer).
    ``process`` defaults to the real ``/proc`` probe; a crash-sweep or unit test may inject
    a fake one — this is the only check below that touches a live OS process."""

    engine: Engine
    process: IProcessProbe = field(default_factory=LinuxProcessProbe)
    clock: IClock = field(default_factory=SystemClock)

    def run(self, *, after_recovery: bool = False) -> list[Violation]:
        """``after_recovery=True`` additionally runs :class:`ActiveLeaseProcessIsLive`.
        That check is sound only once a recovery pass has had its chance: a worker dying
        the instant its daemon does is the CORRECT immediate aftermath of a crash, not a
        violation, before REAP/RESUME ever reconciles it. A live-system or post-convergence
        caller passes ``True``; right after a raw kill -9 leaves it ``False``."""
        violations: list[Violation] = []
        # The audit reads every runner's rows at once, through the host stores' runner audit.
        audit = build_host_stores(
            self.engine, errors=RunnerStoreErrorFactory(get_logger("blizzard.tools.invariants")), clock=self.clock
        ).runner_audit
        with audit.connect() as conn:
            checks: tuple[QueryCheck, ...] = (
                OneLiveLeasePerChunk(conn),
                NoUnownedLiveLeaseProcess(conn),
                UniqueEnvBinding(conn),
                RowsNameRegisteredRunner(conn),
                GaplessOutboundSeq(conn),
                GaplessTranscriptOutboundSeq(conn),
                OutboxCounterAboveSeq(conn),
                TranscriptSegmentFinalizedExactlyOnce(conn),
                OneOpenPauseParkPerLease(conn),
                UsageAttributedOnce(conn),
                NudgeAtMostOnce(conn),
                ChecksRecordedWhenMarked(conn),
                InvocationBoundaryClosedWhenLeaseClosed(conn),
                WorkerBoundaryKindExclusivePerGeneration(conn),
            )
            for check in checks:
                violations.extend(check.run())
            if after_recovery:
                violations.extend(ActiveLeaseProcessIsLive(conn, self.process).run())
            # NOT checked, deliberately: "a pause-parked lease has no closure" — it is
            # false on a legal history; pinned by tests/test_pin_foundation.py.
        return violations


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
    own tenant: its lifecycle facts, its links and their facts, the landings that name one, and the change rows keyed
    by a repository id (a repository's and a project link's). The foreign keys say only that the repository exists,
    not whose it is, so only a read can show a row filed under another tenant's repository."""

    #: Each referring table, its key column for a message, and its column naming the repository.
    REFERRING: ClassVar[tuple[tuple[Table, str, str], ...]] = (
        (hub.repository_lifecycle_facts, "id", "repository_id"),
        (hub.project_repository_links, "project_id", "repository_id"),
        (hub.project_repository_link_facts, "id", "repository_id"),
        (hub.delivery_repo_landed, "id", "repository_id"),
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


@dataclass(frozen=True)
class HubInvariants:
    """The hub store's durable invariants (transitions, epochs, delivery)."""

    engine: Engine
    clock: IClock = field(default_factory=SystemClock)

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        # The query checks audit every open tenant's rows at once; NoRowOfADeletedTenant alone reads a closed one.
        hub_wide = self.engine.execution_options(**{HUB_SCOPED_OPTION: True})
        with hub_wide.connect() as conn:
            checks: tuple[QueryCheck, ...] = (
                OneTransitionPerNodeEpoch(conn),
                EpochConsistentTransitions(conn),
                EpochsOwned(conn),
                RouteSeqUnique(conn),
                OneLiveRoutePerChunk(conn),
                PerRepoLandIdempotent(conn),
                PerRepoMarkerIdempotent(conn),
                NoDoubleDelivery(conn),
                OneLiveExecSlot(conn),
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
                ChunkSourcesLinked(conn),
                LandedRepositoryLinked(conn),
            )
            for check in checks:
                violations.extend(check.run())
        with hub_wide.connect() as conn:
            violations.extend(MigrationsAtomic(conn).run())
            violations.extend(NoRowOfADeletedTenant(conn).run())
            open_tenants = _open_tenants(conn)
        store_connections = HubStoreConnections(self.engine, HubStoreErrorFactory(get_logger("blizzard.hub.store")))
        for tenant in open_tenants:
            # The facts checks load chunks through the tenant's own adapters, one tenant at a time.
            connections = store_connections.scoped(StoreScope(tenant))
            facts_store = ChunkFactsStore(connections, self.clock)
            record_store = ChunkRecordStore(connections, self.clock)
            for facts_check in (
                DerivationAndDelivery(facts_store, record_store),
                LiveRouteHasToken(facts_store, record_store),
            ):
                violations.extend(
                    Violation(found.invariant, f"tenant {tenant}: {found.detail}") for found in facts_check.run()
                )
        return violations


def _open_tenants(conn: Connection) -> list[TenantId]:
    """Every tenant no deletion has closed, in id order. A closed tenant's rows are being drained, a chunk's
    facts before its record, so its chunks are no longer whole and are left out of the facts checks, as its rows are
    out of every :class:`HubQueryCheck`."""
    closed = select(hub.tenant_deletions.c.tenant_id)
    tenants = hub.tenants.c.tenant_id
    return [
        TenantId(t) for t in conn.execute(select(tenants).where(tenants.not_in(closed)).order_by(tenants)).scalars()
    ]


@collaborator
@dataclass(frozen=True)
class Invariants:
    """Both stores' durable invariants, over whichever URLs are given — each store is opened
    read-only over its own engine, and an empty result means every checked invariant holds."""

    runner_db_url: str | None = None
    hub_db_url: str | None = None

    def run(self, *, after_recovery: bool = False) -> list[Violation]:
        """``after_recovery`` rides straight through to :meth:`RunnerInvariants.run` —
        see its own docstring for why that one check needs it."""
        violations: list[Violation] = []
        if self.runner_db_url is not None:
            violations.extend(
                RunnerInvariants(create_engine_from_url(self.runner_db_url)).run(after_recovery=after_recovery)
            )
        if self.hub_db_url is not None:
            violations.extend(HubInvariants(create_engine_from_url(self.hub_db_url)).run())
        return violations
