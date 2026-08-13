"""The operational analytics query seam (blizzard#256) — durations, spend, and outcomes
derived at query time (D1, ``bzh:facts-not-status``) over facts the hub already holds.
New, not an extension of :mod:`queries` (``bzh:controller-read-only``): that module reads
the derived-event projection alone, never a transition or a usage fact."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


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
    id, whichever route served it. A key with zero completed steps never appears, so
    ``avg_seconds`` never divides by zero. Hub-observed wall-clock latency (D3), not a
    runner-measured one."""

    key: str
    completed_steps: int
    total_seconds: float
    avg_seconds: float


@dataclass(frozen=True)
class SpendStats:
    """One grouping key's usage/cost rollup (D6) — the same lower-bound + PARTIAL
    contract :class:`~blizzard.hub.domain.work.UsageTotal` publishes, summed in SQL
    rather than in Python. ``cost_usd`` sums only the rows that carried a cost envelope;
    ``cost_partial`` is ``True`` iff any row in this group lacked one."""

    key: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


@dataclass(frozen=True)
class ChunkSpendRecord:
    """One chunk's own usage/cost rollup — the per-chunk grouping's row (D8), unbounded
    in a wide window and so cursor-paged rather than a single envelope."""

    chunk_id: str
    input_tokens: int
    output_tokens: int
    cache_read_tokens: int
    cache_create_tokens: int
    cost_usd: float
    cost_partial: bool


@dataclass(frozen=True)
class ChunkSpendPage:
    """A bounded, keyset-paginated page of :class:`ChunkSpendRecord` (D8) —
    ``next_cursor`` is ``None`` exactly when this page is the last one, the same
    convention :class:`~blizzard.hub.domain.analytics.queries.EventPage` uses."""

    records: list[ChunkSpendRecord]
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
