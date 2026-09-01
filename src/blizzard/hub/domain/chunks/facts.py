"""The chunk-facts repository seam (blizzard#411) — read-only, D2.

``load_facts``/``load_all_facts`` each project the union of every concept's fact tables for
one chunk (or all chunks); the writes behind that projection are each concept seam's own, so
this seam declares no write half."""

from __future__ import annotations

from typing import Protocol

from blizzard.hub.domain.work import ChunkFacts


class IReadChunkFactsRepository(Protocol):
    """Read-only chunk-facts access — the bounded multi-table projection every derivation
    reads from."""

    def load_facts(self, chunk_id: str) -> ChunkFacts | None: ...
    def load_all_facts(self) -> dict[str, ChunkFacts]:
        """Every non-ephemeral (non-grouped, non-deleted) chunk's complete
        :class:`ChunkFacts`, keyed by chunk id — the fleet-summary bulk read (issue
        #374). A bounded number of queries regardless of fleet size, unlike calling
        :meth:`load_facts` once per chunk; each value is exactly what :meth:`load_facts`
        would return for that chunk id."""
        ...
