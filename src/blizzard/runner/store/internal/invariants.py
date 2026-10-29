"""The runner store's facts-level invariants (``bzh:invariant-checker``): leases, bindings, the outbound buffer."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass
from datetime import datetime
from typing import ClassVar

from sqlalchemy import Connection, Table, and_, func, or_, select

from blizzard.foundation.invariants import QueryCheck, Violation
from blizzard.runner.process.probe import IProcessProbe
from blizzard.runner.store import schema as runner
from blizzard.runner.store.scoped import RUNNER_COLUMN, RUNNER_SCOPING
from blizzard.runner.stores import IRunnerAudit
from blizzard.runner.transcripts.invocation_boundaries import WORKER_STARTING_KINDS


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
            every = set[str]().union(*by_workspace.values())
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
                releases.c.seq > bindings.c.seq,
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


@dataclass(frozen=True)
class RunnerInvariants:
    """The runner store's durable invariants (leases, bindings, outbound buffer).
    ``process`` is the probe the one live-process check reads; a crash-sweep or unit test may inject
    a fake one."""

    audit: IRunnerAudit
    process: IProcessProbe

    def run(self, *, after_recovery: bool = False) -> list[Violation]:
        """``after_recovery=True`` additionally runs :class:`ActiveLeaseProcessIsLive`.
        That check is sound only once a recovery pass has had its chance: a worker dying
        the instant its daemon does is the CORRECT immediate aftermath of a crash, not a
        violation, before REAP/RESUME ever reconciles it. A live-system or post-convergence
        caller passes ``True``; right after a raw kill -9 leaves it ``False``."""
        violations: list[Violation] = []
        # The audit reads every runner's rows at once.
        with self.audit.connect() as conn:
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
