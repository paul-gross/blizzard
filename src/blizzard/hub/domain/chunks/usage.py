"""The chunk-usage repository seam — recorded model spend, per node-step."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import UsageTotal


class IReadChunkUsageRepository(Protocol):
    """Read-only chunk-usage access."""

    def usage_total_since(self, since: datetime, *, until: datetime | None = None) -> UsageTotal:
        """The usage/cost total across every chunk's facts recorded at or after ``since``
        — and, when ``until`` is given, strictly before it (issue #60, issue #183,
        blizzard#517). ``since`` is inclusive and ``until`` exclusive, so adjacent windows
        sharing a boundary instant neither double-count nor drop a fact at it. Omitting
        ``until`` is the original open-ended tail. Follows the lower-bound + PARTIAL cost
        contract in ``UsageTotal.of_grouped_sums``."""
        ...


class IWriteChunkUsageRepository(IReadChunkUsageRepository, Protocol):
    """Read-write chunk-usage access."""

    def record_usage(
        self,
        chunk_id: str,
        *,
        node_id: str,
        epoch: int,
        runner_id: str,
        kind: str,
        model: str,
        harness_id: str | None = None,
        harness_version: str | None = None,
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd: float | None,
        estimated_cost_usd: float | None = None,
        at: datetime,
    ) -> None:
        """Append one ``usage.recorded`` fact — never a stored aggregate.

        Deliberately **not** epoch-fenced: called for every landed usage fact regardless
        of whether ``epoch`` is the chunk's latest, since it is real spend either way.
        Idempotency rides the caller's own applied-seq high-water mark. ``harness_id``/
        ``harness_version``/``estimated_cost_usd`` are recorded, never derived — each
        ``None`` when the caller had none to report (see ``docs/deployment/spend.md``)."""
        ...
