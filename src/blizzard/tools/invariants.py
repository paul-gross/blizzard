"""The facts-level invariant checker (``bzh:invariant-checker``).

After any crash → restart → recover cycle, the durable facts in both stores must still
satisfy the correctness conditions the design rests on. A violation names the exact
broken invariant, so a failure points at the window and the rule. Because both stores
are facts-only (``bzh:facts-not-status``), every check is a plain query; nothing mutates."""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from datetime import datetime

from sqlalchemy import Connection, Engine, func, select

from blizzard.foundation.clock import IClock, SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.foundation.node_steps import Executor
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunks.facts import IReadChunkFactsRepository
from blizzard.hub.domain.chunks.record import IReadChunkRecordRepository
from blizzard.hub.domain.graph import RESERVED_TERMINAL
from blizzard.hub.domain.work import ChunkFacts, MigrationSource, RouteHistory
from blizzard.hub.store import schema as hub
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_record_store import ChunkRecordStore
from blizzard.hub.store.internal.chunk_rows import DEFAULT_MODEL
from blizzard.runner.store import schema as runner


@dataclass(frozen=True)
class Violation:
    invariant: str
    detail: str

    def __str__(self) -> str:
        return f"[{self.invariant}] {self.detail}"


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
class FactsCheck(Check):
    """An invariant answerable only per chunk, over its loaded facts."""

    facts: IReadChunkFactsRepository
    record: IReadChunkRecordRepository

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        for chunk in self.record.list_all():
            violations.extend(self.for_chunk(chunk.chunk_id, self.facts.load_facts(chunk.chunk_id)))
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


class UniqueEnvBinding(QueryCheck):
    """A held env id (a binding with no release fact) is bound to at most one chunk. Two
    chunks sharing a held env would double-book it."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        by_env: dict[str, set[str]] = {}
        for chunk_id, env_id in self._held_bindings():
            by_env.setdefault(env_id, set()).add(chunk_id)
        for env_id, chunks in by_env.items():
            if len(chunks) > 1:
                violations.append(
                    Violation("runner:unique-env-binding", f"env {env_id} held by chunks {sorted(chunks)}")
                )
        return violations

    def _held_bindings(self) -> list[tuple[str, str]]:
        releases = {
            (row[0], row[1])
            for row in self.conn.execute(
                select(runner.binding_releases.c.chunk_id, runner.binding_releases.c.environment_id)
            )
        }
        held: list[tuple[str, str]] = []
        for chunk_id, env_id in self.conn.execute(
            select(runner.env_bindings.c.chunk_id, runner.env_bindings.c.environment_id)
        ):
            if (chunk_id, env_id) not in releases:
                held.append((chunk_id, env_id))
        return held


class GaplessOutboundSeq(QueryCheck):
    """A hole in the outbound buffer's seqs would break FIFO idempotent replay."""

    def run(self) -> list[Violation]:
        seqs = sorted(row[0] for row in self.conn.execute(select(runner.outbound_buffer.c.seq)))
        if not seqs:
            return []
        expected = list(range(seqs[0], seqs[0] + len(seqs)))
        if seqs == expected:
            return []
        missing = sorted(set(expected) - set(seqs))
        return [Violation("runner:gapless-outbound-seq", f"outbound seqs not gapless; missing {missing}")]


class GaplessTranscriptOutboundSeq(QueryCheck):
    """A hole in the transcript lane's own *pending* (unacked) seqs means a lost record
    (D3, issue #246) — scoped to the pending window, not every seq ever minted (review
    since an acked non-final row is pruned outright."""

    def run(self) -> list[Violation]:
        seqs = sorted(
            row[0]
            for row in self.conn.execute(
                select(runner.transcript_outbound_buffer.c.seq).where(
                    runner.transcript_outbound_buffer.c.acked_at.is_(None)
                )
            )
        )
        if not seqs:
            return []
        expected = list(range(seqs[0], seqs[0] + len(seqs)))
        if seqs == expected:
            return []
        missing = sorted(set(expected) - set(seqs))
        return [
            Violation(
                "runner:gapless-transcript-outbound-seq",
                f"pending transcript outbound seqs not gapless; missing {missing}",
            )
        ]


class TranscriptSegmentFinalizedExactlyOnce(QueryCheck):
    """A finalized segment (`finalized_at` set) has exactly one `final` row buffered for it —
    a step's segments are final by step close, landed once, never zero and never duplicated
    (issue #246). Unconditional: every segment has a `normalizer_version` to declare, so this
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
    """An *open* pause-park is a park fact with no pause-resume at or after it (issue #46);
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
        """The plain-query mirror of the store adapter's ``OPEN_PAUSE_PARK`` — same ``>=``
        (a same-instant resume is a resume) and same per-lease correlation."""
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
            if not any(r >= parked_at for r in resumes.get(lease_id, ()))
        ]


class UsageAttributedOnce(QueryCheck):
    """A harness invocation's usage is attributed once per (lease, generation, kind) (epic #57, issue #58)."""

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


class NudgeAtMostOnce(QueryCheck):
    """A lease's ``produces``-unmet nudge fires at most once per (lease, epoch) (issue #113)."""

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
    """A ``checks_ran`` marker implies its check result rows exist (issue #114)."""

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


class OneTransitionPerNodeEpoch(QueryCheck):
    """At most one accepted transition per (chunk, from_node, epoch): the idempotency
    guarantee. A duplicate is a double-apply."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        key = Counter(
            (row[0], row[1], row[2])
            for row in self.conn.execute(
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


class EpochConsistentTransitions(QueryCheck):
    """No accepted transition carries an epoch greater than the chunk's latest lease fact;
    a higher one means a zombie landed."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        latest_lease = {
            row[0]: row[1]
            for row in self.conn.execute(
                select(hub.lease_facts.c.chunk_id, func.max(hub.lease_facts.c.epoch)).group_by(
                    hub.lease_facts.c.chunk_id
                )
            )
        }
        for chunk_id, max_epoch in self.conn.execute(
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


class RouteSeqUnique(QueryCheck):
    """Per-chunk route ``seq`` is unique across ``route_created`` + ``route_released`` +
    ``route_token_minted`` combined (issues #41, #84a)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        route_seqs = Counter(
            (row[0], row[1]) for row in self.conn.execute(select(hub.route_created.c.chunk_id, hub.route_created.c.seq))
        )
        route_seqs.update(
            (row[0], row[1])
            for row in self.conn.execute(select(hub.route_released.c.chunk_id, hub.route_released.c.seq))
        )
        route_seqs.update(
            (row[0], row[1])
            for row in self.conn.execute(select(hub.route_token_minted.c.chunk_id, hub.route_token_minted.c.seq))
        )
        for (chunk_id, seq), n in route_seqs.items():
            if n > 1:
                violations.append(
                    Violation("hub:route-seq-unique", f"chunk {chunk_id} seq {seq} used by {n} route events")
                )
        return violations


class PerRepoLandIdempotent(QueryCheck):
    """A redelivery skips already-landed repos, so a second landed fact is a double land."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        repo_lands = Counter(
            (row[0], row[1])
            for row in self.conn.execute(select(hub.delivery_repo_landed.c.chunk_id, hub.delivery_repo_landed.c.repo))
        )
        for (chunk_id, repo), n in repo_lands.items():
            if n > 1:
                violations.append(
                    Violation("hub:per-repo-land-idempotent", f"chunk {chunk_id} repo {repo} landed {n} times")
                )
        return violations


class PerRepoMarkerIdempotent(QueryCheck):
    """At most one ``merged/<repo>`` marker artifact per (chunk, node, epoch, name) (issue #67)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        markers = Counter(
            (row[0], row[1], row[2], row[3])
            for row in self.conn.execute(
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


class PrOpenedIdempotent(QueryCheck):
    """At most one pr.opened fact per (chunk, repo); also guarded by ``uq_delivery_pr_opened_chunk_repo``."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        pr_opens = Counter(
            (row[0], row[1])
            for row in self.conn.execute(select(hub.delivery_pr_opened.c.chunk_id, hub.delivery_pr_opened.c.repo))
        )
        for (chunk_id, repo), n in pr_opens.items():
            if n > 1:
                violations.append(
                    Violation("hub:pr-opened-idempotent", f"chunk {chunk_id} repo {repo} has {n} pr.opened facts")
                )
        return violations


class NoDoubleDelivery(QueryCheck):
    """At most one whole-chunk delivery.landed terminal fact."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        landed = Counter(row[0] for row in self.conn.execute(select(hub.delivery_landed.c.chunk_id)))
        for chunk_id, n in landed.items():
            if n > 1:
                violations.append(
                    Violation("hub:no-double-delivery", f"chunk {chunk_id} has {n} delivery.landed facts")
                )
        return violations


class NoDoubleTerminalClosure(QueryCheck):
    """At most one terminal (``closed``/``gone``) ``work_item_closures`` outcome per
    ``(chunk, source, ref)`` — the drain's idempotency guard (blizzard#383) is a
    ``retired_at`` check, not a store-level constraint, so a bug there would otherwise
    go unseen: the schema itself permits ``closed`` and ``gone`` to coexist."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        key = Counter(
            (row.chunk_id, row.source, row.ref)
            for row in self.conn.execute(
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


class NoPendingIntentAgainstATerminalRef(QueryCheck):
    """A pending (``retired_at IS NULL``) ``close_intents`` row whose ``(chunk, source,
    ref)`` already carries a terminal closure outcome is a stuck retirement — the drain
    recorded the outcome but never retired the intent that rode it (blizzard#383)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        terminal = {
            (row.chunk_id, row.source, row.ref)
            for row in self.conn.execute(
                select(
                    hub.work_item_closures.c.chunk_id, hub.work_item_closures.c.source, hub.work_item_closures.c.ref
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone"]))
            )
        }
        for row in self.conn.execute(
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


class NoUnenqueuedClosableRef(QueryCheck):
    """A non-ephemeral chunk that has landed or been hand-completed owes a ``close_intents``
    row for every still-open ``chunk_work_refs`` ref (D1, blizzard#383) — the nine
    call-site-guarded ``_enqueue_close_intents`` invocations' own derived invariant. A terminal
    outcome or any ``close_intents`` row (retired or not) satisfies it; neither is a missed call."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        ephemeral = {row[0] for row in self.conn.execute(select(hub.chunk_grouped.c.chunk_id))} | {
            row[0] for row in self.conn.execute(select(hub.chunk_deleted.c.chunk_id))
        }
        landed = (
            {row[0] for row in self.conn.execute(select(hub.delivery_landed.c.chunk_id))}
            | {row[0] for row in self.conn.execute(select(hub.delivery_repo_landed.c.chunk_id))}
            | {
                row[0]
                for row in self.conn.execute(
                    select(hub.artifacts.c.chunk_id).where(hub.artifacts.c.name.like("merged/%"))
                )
            }
        )
        completed = {row[0] for row in self.conn.execute(select(hub.chunk_completed.c.chunk_id))}
        eligible = (landed | completed) - ephemeral
        if not eligible:
            return violations
        terminal = {
            (row.chunk_id, row.source, row.ref)
            for row in self.conn.execute(
                select(
                    hub.work_item_closures.c.chunk_id, hub.work_item_closures.c.source, hub.work_item_closures.c.ref
                ).where(hub.work_item_closures.c.outcome.in_(["closed", "gone"]))
            )
        }
        enqueued = {
            (row.chunk_id, row.source, row.ref)
            for row in self.conn.execute(
                select(hub.close_intents.c.chunk_id, hub.close_intents.c.source, hub.close_intents.c.ref)
            )
        }
        for row in self.conn.execute(
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


class OneLiveExecSlot(QueryCheck):
    """At most one hub_exec_slot row is live (``released_at IS NULL``) at a time (#65;
    pinned by tests/test_pin_foundation.py)."""

    def run(self) -> list[Violation]:
        live_slots = self.conn.execute(
            select(func.count()).select_from(hub.hub_exec_slot).where(hub.hub_exec_slot.c.released_at.is_(None))
        ).scalar()
        if (live_slots or 0) > 1:
            return [Violation("hub:one-live-exec-slot", f"{live_slots} hub-execution slots are live at once")]
        return []


class MigrationsAtomic(QueryCheck):
    """``hub:one-migration-per-node-epoch`` — one row per ``(chunk, from_node, epoch)``;
    ``hub:migration-pin-consistent`` — the chunk carries the newest migration's target pin;
    ``hub:migration-route-released`` — a runner landing released the route (a hub landing, issue #111,
    and an operator restart's own re-pin, #371, both keep it by design)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        key = Counter(
            (row[0], row[1], row[2])
            for row in self.conn.execute(
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
        for m in self.conn.execute(select(hub.chunk_migrations)):
            cur = newest.get(m.chunk_id)
            if cur is None or (m.recorded_at, m.epoch) >= (cur.recorded_at, cur.epoch):  # type: ignore[attr-defined]
                newest[m.chunk_id] = m
        chunks = {c.chunk_id: c for c in self.conn.execute(select(hub.chunks))}
        # A migration's landed node executor (issue #111). Node ids are globally-unique, so
        # one node_id -> executor map resolves any landing node.
        landed_executor = {
            row.node_id: row.executor
            for row in self.conn.execute(select(hub.graph_nodes.c.node_id, hub.graph_nodes.c.executor))
        }
        # The latest route release per chunk — a runner-landing migration's ``recorded_at``
        # is never above the chunk's newest release.
        latest_release: dict[str, datetime] = {}
        for r in self.conn.execute(select(hub.route_released.c.chunk_id, hub.route_released.c.released_at)):
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
            # **Membership**, not equality against `[model_after]` (issue #144) — the list may
            # legitimately grow afterwards (pinned by tests/test_invariant_checker.py).
            elif m.model_after is not None and m.model_after not in DEFAULT_MODEL.decode(chunk.default_model):  # type: ignore[attr-defined]
                violations.append(
                    Violation(
                        "hub:migration-pin-consistent",
                        f"chunk {chunk_id} default_model {chunk.default_model!r} does not carry "  # type: ignore[attr-defined]
                        f"{m.model_after}, which its newest migration re-pinned",  # type: ignore[attr-defined]
                    )
                )
            # A hub landing (issue #111) and an operator restart's own re-pin (#371) both retain the
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
        # Both terminal delivery facts require the terminal transition (issue #63):
        # ``delivery.landed`` and ``pr.closed``. An *open* PR is parked, so it is not flagged.
        if facts.delivery_landed or facts.pr_closed:
            newest = max(facts.transitions, key=lambda t: (t.recorded_at, t.epoch), default=None)
            if newest is None or newest.to_node_id != RESERVED_TERMINAL:
                target = None if newest is None else newest.to_node_id
                fact = "delivery.landed" if facts.delivery_landed else "pr.closed"
                return [
                    Violation(
                        "hub:merge-queue-single-state",
                        f"chunk {chunk_id} is {fact} but newest transition targets {target}",
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
    """The runner store's durable invariants (leases, bindings, outbound buffer)."""

    engine: Engine

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        with self.engine.connect() as conn:
            checks: tuple[QueryCheck, ...] = (
                OneLiveLeasePerChunk(conn),
                UniqueEnvBinding(conn),
                GaplessOutboundSeq(conn),
                GaplessTranscriptOutboundSeq(conn),
                TranscriptSegmentFinalizedExactlyOnce(conn),
                OneOpenPauseParkPerLease(conn),
                UsageAttributedOnce(conn),
                NudgeAtMostOnce(conn),
                ChecksRecordedWhenMarked(conn),
            )
            for check in checks:
                violations.extend(check.run())
            # NOT checked, deliberately: "a pause-parked lease has no closure" (issue #46) — it is
            # false on a legal history; pinned by tests/test_pin_foundation.py.
        return violations


@dataclass(frozen=True)
class SegmentChunkResolves(QueryCheck):
    """Every ``transcript_segments.chunk_id`` resolves to a ``chunks`` row — the foreign key
    the table declares, which sqlite does not enforce. A breach hides a chunk's transcripts
    from every read that joins through the chunk, so transcript-event derivation declines the
    segment rather than deriving a node-step it cannot place."""

    def run(self) -> list[Violation]:
        minted = {row[0] for row in self.conn.execute(select(hub.chunks.c.chunk_id))}
        orphans = {
            (row.segment_id, row.chunk_id)
            for row in self.conn.execute(
                select(hub.transcript_segments.c.segment_id, hub.transcript_segments.c.chunk_id).distinct()
            )
            if row.chunk_id not in minted
        }
        return [
            Violation("hub:segment-chunk-resolves", f"segment {segment_id} names unknown chunk {chunk_id}")
            for segment_id, chunk_id in sorted(orphans)
        ]


class NoStandingDependencyCycle(QueryCheck):
    """The standing (unreleased) ``chunk_dependencies`` edges form no cycle — a derived
    cross-fact invariant with no engine constraint behind it, held only by
    ``DependencyService`` under the claim lock (issue #456)."""

    def run(self) -> list[Violation]:
        graph: dict[str, list[str]] = {}
        for row in self.conn.execute(
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


class NoDuplicateStandingDependency(QueryCheck):
    """At most one standing (unreleased) edge per ordered ``(dependent, prerequisite)``
    pair — a durable invariant held only by domain code under the claim lock, since
    ``chunk_dependencies`` carries no database uniqueness constraint on the pair
    (issue #456)."""

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        pairs = Counter(
            (row.dependent_chunk_id, row.prerequisite_chunk_id)
            for row in self.conn.execute(
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


class NoStandingDependencyOntoEphemeralChunk(QueryCheck):
    """A standing (unreleased) edge whose dependent or prerequisite is ephemeral (grouped away or deleted) — its FK
    still resolves, so the engine enforces nothing here. Design upholds this by construction now that
    ``DeleteService`` and ``GroupService`` both share the claim lock; this check is the backstop that would catch a
    regression, not a guard against a known-live gap."""

    def run(self) -> list[Violation]:
        ephemeral = {row[0] for row in self.conn.execute(select(hub.chunk_grouped.c.chunk_id))} | {
            row[0] for row in self.conn.execute(select(hub.chunk_deleted.c.chunk_id))
        }
        if not ephemeral:
            return []
        violations: list[Violation] = []
        for row in self.conn.execute(
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


@dataclass(frozen=True)
class HubInvariants:
    """The hub store's durable invariants (transitions, epochs, delivery)."""

    engine: Engine
    clock: IClock = field(default_factory=SystemClock)

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        with self.engine.connect() as conn:
            checks: tuple[QueryCheck, ...] = (
                OneTransitionPerNodeEpoch(conn),
                EpochConsistentTransitions(conn),
                RouteSeqUnique(conn),
                PerRepoLandIdempotent(conn),
                PerRepoMarkerIdempotent(conn),
                PrOpenedIdempotent(conn),
                NoDoubleDelivery(conn),
                OneLiveExecSlot(conn),
                NoDoubleTerminalClosure(conn),
                NoPendingIntentAgainstATerminalRef(conn),
                NoUnenqueuedClosableRef(conn),
                SegmentChunkResolves(conn),
                NoStandingDependencyCycle(conn),
                NoDuplicateStandingDependency(conn),
                NoStandingDependencyOntoEphemeralChunk(conn),
            )
            for check in checks:
                violations.extend(check.run())
        with self.engine.connect() as conn:
            violations.extend(MigrationsAtomic(conn).run())
        store_connections = HubStoreConnections(self.engine, HubStoreErrorFactory(get_logger("blizzard.hub.store")))
        facts_store = ChunkFactsStore(store_connections, self.clock)
        record_store = ChunkRecordStore(store_connections, self.clock, facts=facts_store)
        for facts_check in (
            DerivationAndDelivery(facts_store, record_store),
            LiveRouteHasToken(facts_store, record_store),
        ):
            violations.extend(facts_check.run())
        return violations


@dataclass(frozen=True)
class Invariants:
    """Both stores' durable invariants, over whichever URLs are given — each store is opened
    read-only over its own engine, and an empty result means every checked invariant holds."""

    runner_db_url: str | None = None
    hub_db_url: str | None = None

    def run(self) -> list[Violation]:
        violations: list[Violation] = []
        if self.runner_db_url is not None:
            violations.extend(RunnerInvariants(create_engine_from_url(self.runner_db_url)).run())
        if self.hub_db_url is not None:
            violations.extend(HubInvariants(create_engine_from_url(self.hub_db_url)).run())
        return violations
