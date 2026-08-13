"""The operational analytics query seam (blizzard#256) — durations, spend, and outcomes
derived at query time (D1, ``bzh:facts-not-status``) over facts the hub already holds.
New, not an extension of :mod:`queries` (``bzh:controller-read-only``): that module reads
the derived-event projection alone. The D2/D5 folds below are pure functions over
already-loaded facts (``bzh:domain-core``) — the adapter fetches and maps, this module
decides."""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import UsageTotal


@dataclass(frozen=True)
class OperationalCriteria:
    """Every filter the operational datasets owe (blizzard#256 D7) — the scope shared
    with events and counts, narrowed to the four fields that mean something outside the
    derived-event projection: no ``extractor_version``, no event-shape filter."""

    graph_id: str | None = None
    source: str | None = None
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class DurationStats:
    """One grouping key's step-duration rollup (D2/D3) — ``key`` is a node id or a graph
    id, whichever route served it. Hub-observed wall-clock latency (D3), runner-executed
    steps only — a hub step's own exit shares its synthetic lease mint's instant by
    construction, so it never carries a real duration."""

    key: str
    completed_steps: int
    total_seconds: float
    avg_seconds: float


@dataclass(frozen=True)
class SpendStats:
    """One grouping key's usage/cost rollup (D6, D8) — ``key`` is a node, graph, or chunk
    id (one shape for all three). Holds a
    :class:`~blizzard.hub.domain.work.UsageTotal`, not its fields flattened apart, so the
    lower-bound + PARTIAL contract keeps its one owner (``canon:one-owner``)."""

    key: str
    total: UsageTotal


@dataclass(frozen=True)
class ChunkSpendPage:
    """A bounded, keyset-paginated page of :class:`SpendStats` keyed by chunk id (D8) —
    ``next_cursor`` is ``None`` exactly when this page is the last one, the same
    convention :class:`~blizzard.hub.domain.analytics.queries.EventPage` uses."""

    records: list[SpendStats]
    next_cursor: str | None


@dataclass(frozen=True)
class OutcomeStats:
    """One node's judged-choice distribution and attempt-failure count (D4) — two
    distinct quantities, never blended: a judged failure edge consumes no retry budget,
    while a crash, verdict-less exit, or reap does. A delivery kick-back
    (``chunk_bounces``) counts as neither."""

    node_id: str
    choice_counts: dict[str, int]
    attempt_failures: int


# --- D2/D5 pure folds — typed facts in, decided rows out ------------------------------


@dataclass(frozen=True)
class TransitionMovement:
    """One ``transitions`` row already narrowed to what D2/D5 read — never touches the
    domain's own :class:`~blizzard.hub.domain.work.TransitionFact`, which carries fields
    (``choice_name``, executor) status derivation needs and these folds don't."""

    chunk_id: str
    epoch: int
    transition_id: str
    from_node_id: str | None
    to_node_id: str
    graph_id: str
    recorded_at: datetime


@dataclass(frozen=True)
class MigrationMovement:
    """One ``chunk_migrations`` row narrowed the same way as :class:`TransitionMovement`.
    ``from_graph_id`` is the only record of what graph an *earlier* attempt ran in once a
    later migration re-pins the chunk elsewhere."""

    chunk_id: str
    epoch: int
    migration_id: str
    landed_node_id: str | None
    from_graph_id: str
    to_graph_id: str
    recorded_at: datetime


@dataclass(frozen=True)
class LeaseEpoch:
    """One deduped ``(chunk_id, epoch)``'s earliest mint (A7) — a candidate attempt D5
    resolves a node for, or a duration fold's own interval start."""

    chunk_id: str
    epoch: int
    minted_at: datetime


@dataclass(frozen=True)
class StepDuration:
    """One measured step interval (D2/D3): the exited node (``None`` for the first
    movement out of entry), the graph, its seconds, and the exiting transition's own
    ``recorded_at`` — the last carried through so :func:`steps_in_window` can filter the
    fold's output by it."""

    from_node_id: str | None
    graph_id: str
    seconds: float
    recorded_at: datetime


def fold_step_durations(
    transitions: Sequence[TransitionMovement], lease_min_by_epoch: Mapping[tuple[str, int], datetime]
) -> list[StepDuration]:
    """D2/D3: one interval per transition, chained within its own ``(chunk_id, epoch)``
    group (two+ can share one, e.g. a gate's entry and later resolution) — the first
    measures from the lease mint, each later one from its predecessor. Needs each
    group's WHOLE history: window by narrowing which groups are fetched, or by filtering
    the output with :func:`steps_in_window` — never by narrowing ``transitions`` itself."""
    by_group: dict[tuple[str, int], list[TransitionMovement]] = {}
    for t in transitions:
        by_group.setdefault((t.chunk_id, t.epoch), []).append(t)
    out: list[StepDuration] = []
    for (chunk_id, epoch), rows in by_group.items():
        start = lease_min_by_epoch.get((chunk_id, epoch))
        if start is None:  # pragma: no cover - a transition never accepts without a live lease
            continue
        for t in sorted(rows, key=lambda t: (t.recorded_at, t.transition_id)):
            seconds = (t.recorded_at - start).total_seconds()
            out.append(
                StepDuration(
                    from_node_id=t.from_node_id, graph_id=t.graph_id, seconds=seconds, recorded_at=t.recorded_at
                )
            )
            start = t.recorded_at
    return out


def steps_in_window(
    rows: Sequence[StepDuration], *, since: datetime | None, until: datetime | None
) -> list[StepDuration]:
    """Keep only :func:`fold_step_durations`' rows whose own transition falls in
    ``[since, until)`` — applied AFTER the fold chains a group's full history, so a window
    edge inside a multi-transition epoch drops the excluded row's *output* only, never
    its place in the chain."""
    return [
        row
        for row in rows
        if (since is None or row.recorded_at >= since) and (until is None or row.recorded_at < until)
    ]


def summarize_durations(rows: Sequence[StepDuration], *, key: str) -> list[DurationStats]:
    """Roll :func:`fold_step_durations`' rows up by ``"node"`` or ``"graph"``. A row with
    no node (the first movement out of entry, ``from_node_id`` is ``None``) is skipped
    from the node rollup but still counted in the graph one — it happened in a graph even
    though it has no exiting node to attribute to."""
    totals: dict[str, float] = {}
    counts: dict[str, int] = {}
    for row in rows:
        group_key = row.from_node_id if key == "node" else row.graph_id
        if group_key is None:
            continue
        totals[group_key] = totals.get(group_key, 0.0) + row.seconds
        counts[group_key] = counts.get(group_key, 0) + 1
    return [
        DurationStats(key=k, completed_steps=counts[k], total_seconds=totals[k], avg_seconds=totals[k] / counts[k])
        for k in sorted(counts)
    ]


def resolve_attempt_failures(
    *,
    lease_epochs: Sequence[LeaseEpoch],
    transitions: Sequence[TransitionMovement],
    migrations: Sequence[MigrationMovement],
    bounced: Sequence[tuple[str, int]],
    chunk_graph: Mapping[str, str],
    chunk_max_lease_epoch: Mapping[str, int],
    graph_entry_node: Mapping[str, str],
    graph_id_filter: str | None,
) -> dict[str, int]:
    """D5: count a node for every candidate lease epoch genuinely over — bounced or
    superseded — and unresolved by a transition/migration of its own. The node is the
    chunk's latest movement below that epoch (a tie favors the migration), or with no
    movement at all, the graph the chunk ran in AT that epoch — not its current pin,
    which a later migration can have moved on from."""
    bounced_set = set(bounced)
    resolved: set[tuple[str, int]] = {(t.chunk_id, t.epoch) for t in transitions} | {
        (m.chunk_id, m.epoch) for m in migrations
    }
    transitions_by_chunk: dict[str, list[TransitionMovement]] = {}
    for t in transitions:
        transitions_by_chunk.setdefault(t.chunk_id, []).append(t)
    migrations_by_chunk: dict[str, list[MigrationMovement]] = {}
    for m in migrations:
        migrations_by_chunk.setdefault(m.chunk_id, []).append(m)

    failures: dict[str, int] = {}
    for lease in lease_epochs:
        chunk_id, epoch = lease.chunk_id, lease.epoch
        if (chunk_id, epoch) in bounced_set:
            continue
        if (chunk_id, epoch) in resolved:
            continue
        if epoch == chunk_max_lease_epoch.get(chunk_id):
            continue

        # (recorded_at, epoch, kind_rank): kind_rank breaks a same-instant tie toward
        # the migration, mirroring ChunkFacts._latest_movement_is_migration.
        candidates: list[tuple[datetime, int, int, str, str]] = [
            (t.recorded_at, t.epoch, 0, t.to_node_id, t.graph_id)
            for t in transitions_by_chunk.get(chunk_id, ())
            if t.epoch < epoch
        ]
        candidates += [
            (
                m.recorded_at,
                m.epoch,
                1,
                m.landed_node_id if m.landed_node_id is not None else graph_entry_node[m.to_graph_id],
                m.to_graph_id,
            )
            for m in migrations_by_chunk.get(chunk_id, ())
            if m.epoch < epoch
        ]
        if candidates:
            *_, node_id, graph_id = max(candidates)
        else:
            # No movement: the current pin is stale if a later migration re-pinned the
            # chunk — use that migration's own `from_graph_id` instead when one exists.
            later_migrations = [m for m in migrations_by_chunk.get(chunk_id, ()) if m.epoch >= epoch]
            if later_migrations:
                earliest_later = min(later_migrations, key=lambda m: (m.epoch, m.recorded_at))
                graph_id = earliest_later.from_graph_id
            else:
                graph_id = chunk_graph[chunk_id]
            node_id = graph_entry_node[graph_id]

        if graph_id_filter is not None and graph_id != graph_id_filter:
            continue
        failures[node_id] = failures.get(node_id, 0) + 1
    return failures


class IReadOperationalAnalytics(Protocol):
    """Read-only operational-datasets query Protocol (blizzard#256 D1) — the durations,
    spend, and outcomes routes' own seam (``bzh:controller-read-only``,
    ``bzh:repository-split``). No write repository backs it: every dataset here is
    derived at read time over facts other services already write."""

    def durations_by_node(self, criteria: OperationalCriteria) -> list[DurationStats]:
        """Completed-step duration rollups grouped by the step's node (D2) — one entry
        per node id that had at least one completed step matching ``criteria``, ordered
        by ``key`` ascending."""
        ...

    def durations_by_graph(self, criteria: OperationalCriteria) -> list[DurationStats]:
        """The same rollup grouped by the step's graph (D2) instead of its node — the
        graph the transition itself happened in, never the chunk's current pin."""
        ...

    def spend_by_node(self, criteria: OperationalCriteria) -> list[SpendStats]:
        """Usage/cost rollups grouped by ``usage_facts.node_id`` (D6), ordered by
        ``key`` ascending."""
        ...

    def spend_by_graph(self, criteria: OperationalCriteria) -> list[SpendStats]:
        """The same rollup grouped by each usage fact's chunk's *current* graph pin — a
        chunk that migrated attributes every usage fact it ever recorded to where it
        lives today, not to the graph it was in when the cost was incurred (a documented
        simplification: ``usage_facts`` carries no ``graph_id`` of its own, and is
        deliberately not epoch-fenced either, D6)."""
        ...

    def spend_by_chunk(self, criteria: OperationalCriteria, *, cursor: str | None = None, limit: int) -> ChunkSpendPage:
        """At most ``limit`` (at least 1, else ``ValueError``) chunks' usage/cost
        rollups matching ``criteria``, ordered by ``chunk_id`` ascending — a total order
        two identical calls agree on. ``cursor`` is a prior
        :attr:`ChunkSpendPage.next_cursor`: any other value raises
        :class:`~blizzard.hub.domain.analytics.queries.MalformedCursor`."""
        ...

    def outcomes_by_node(self, criteria: OperationalCriteria) -> list[OutcomeStats]:
        """Judged-choice distribution and attempt-failure counts grouped by node (D4/D5),
        ordered by ``node_id`` ascending — a node with neither a judged choice nor an
        attempt failure matching ``criteria`` never appears."""
        ...
