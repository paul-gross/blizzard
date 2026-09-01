"""The chunk-usage repository seam — recorded model spend, per node-step."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from blizzard.hub.domain.work import UsageFact


class IReadChunkUsageRepository(Protocol):
    """Read-only chunk-usage access."""

    def usage_since(self, since: datetime, *, until: datetime | None = None) -> list[UsageFact]:
        """Every usage fact recorded at or after ``since`` — and, when ``until`` is given,
        strictly before it — across every chunk (issue #60, issue #183). ``since`` is
        inclusive and ``until`` exclusive, so adjacent windows sharing a boundary instant
        neither double-count nor drop a fact at it. Omitting ``until`` is the original
        open-ended tail. The caller derives the total via ``UsageTotal.of``."""
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
        input_tokens: int,
        output_tokens: int,
        cache_read_tokens: int,
        cache_create_tokens: int,
        cost_usd: float | None,
        at: datetime,
    ) -> None:
        """Append one ``usage.recorded`` fact (issue #59) — never a stored aggregate.

        Deliberately **not** epoch-fenced: called for every landed usage fact regardless
        of whether ``epoch`` is the chunk's latest, since it is real spend either way.
        Idempotency rides the caller's own applied-seq high-water mark."""
        ...
