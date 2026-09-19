"""The chunk-facts repository seam — read-only, D2.

``load_facts``/``load_all_facts`` each project the union of every concept's fact tables for
one chunk (or all chunks); the writes behind that projection are each concept seam's own, so
this seam declares no write half."""

from __future__ import annotations

from collections.abc import Sequence
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
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

    def load_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts`'s batched sibling — every id's :class:`ChunkFacts`, keyed by
        chunk id, in a bounded number of queries per id batch rather than one query pair
        per id. An id that doesn't exist or is ephemeral is silently dropped, the same as
        :meth:`load_facts` returning ``None`` for it."""
        ...

    def load_all_statuses(self) -> dict[str, ChunkStatus]:
        """Every non-ephemeral chunk's derived :class:`ChunkStatus`, keyed by chunk id —
        `load_all_facts`'s status-only projection, reading only the fact families
        :meth:`ChunkFacts.status` reaches rather than every family `load_all_facts`
        loads."""
        ...

    def status_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts_for`'s status-only sibling — every id's :class:`ChunkFacts`, keyed
        by chunk id, reading only the fact families behind status, pause, latest epoch,
        restart epochs, cost, and open decision (blizzard#521) rather than every family
        `load_facts_for` loads. An id that doesn't exist or is ephemeral is silently
        dropped, the same as `load_facts_for`."""
        ...
