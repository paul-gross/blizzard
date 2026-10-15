"""The chunk-facts repository seam — read-only.

``load_facts``/``load_all_facts`` each project the union of every concept's fact tables for
one chunk (or all chunks); the writes behind that projection are each concept seam's own, so
this seam declares no write half."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import datetime
from typing import Protocol

from blizzard.foundation.chunk_status import ChunkStatus
from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.projects.model import ProjectId


class IReadChunkFactsRepository(Protocol):
    """Read-only chunk-facts access — the bounded multi-table projection every derivation
    reads from."""

    def load_facts(self, chunk_id: str) -> ChunkFacts | None: ...
    def load_all_facts(self) -> dict[str, ChunkFacts]:
        """Every non-ephemeral (non-grouped, non-deleted) chunk's complete
        :class:`ChunkFacts`, keyed by chunk id — the fleet-summary bulk read.
        A bounded number of queries regardless of fleet size, unlike calling
        :meth:`load_facts` once per chunk; each value is exactly what :meth:`load_facts`
        would return for that chunk id."""
        ...

    def load_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts`'s batched sibling (`bzh:bulk-reconstitution`) — every id's
        :class:`ChunkFacts`, keyed by chunk id. An id that doesn't exist or is ephemeral
        is silently dropped, the same as :meth:`load_facts` returning ``None`` for it."""
        ...

    def movement_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """The movement families needed by :meth:`ChunkFacts.current_node_id`, for the named chunks."""
        ...

    def load_live_statuses(self) -> dict[str, ChunkStatus]:
        """Every non-ephemeral, non-terminal chunk's derived :class:`ChunkStatus`, keyed by
        chunk id (``bzh:live-set-read``) — terminal chunks are excluded in the store query,
        before anything is loaded or derived per chunk. A caller needing a terminal status
        (a done prerequisite) resolves it by id through :meth:`status_facts_for`."""
        ...

    def status_counts(self, *, project: ProjectId | None = None) -> dict[ChunkStatus, int]:
        """How many non-ephemeral chunks derive each :class:`ChunkStatus` — every status keyed,
        zero-filled; the same set ``list_all`` returns, or ``project``'s chunks of it.
        Settled-terminal chunks are counted in the store query without being loaded
        (``bzh:live-set-read``); only the candidates a terminal fact doesn't already settle are
        derived per chunk."""
        ...

    def done_since_count(self, since: datetime, *, project: ProjectId | None = None) -> int:
        """How many non-ephemeral chunks derive ``done`` with a completion instant at or after ``since`` — over the
        set :meth:`status_counts` counts, or ``project``'s chunks of it. Settled-done chunks are counted in the
        store query without being loaded (``bzh:live-set-read``); only the candidates a terminal fact doesn't
        already settle are derived per chunk."""
        ...

    def status_facts_for(self, chunk_ids: Sequence[str]) -> dict[str, ChunkFacts]:
        """`load_facts_for`'s status-only sibling — every id's :class:`ChunkFacts`, keyed
        by chunk id, reading only the fact families behind status, pause, latest epoch,
        restart epochs, cost, and open decision rather than every family
        `load_facts_for` loads. An id that doesn't exist or is ephemeral is silently
        dropped, the same as `load_facts_for`."""
        ...
