"""The analytics event query seam (blizzard#255 D1/D6) — filterable events plus canned
counts, over the projection :mod:`extraction` and :mod:`derivation` populate.

New, not an extension of :mod:`events` (``bzh:controller-read-only``): ``events.py``'s
``IReadTranscriptEvents`` carries only derivation bookkeeping — ``visible_segment_ids``,
``derivation_marker``, and friends — never an event query. The routes (Phase 3) depend on
this Protocol alone; no write repository backs them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol


@dataclass(frozen=True)
class EventQueryCriteria:
    """Every filter this API owes (blizzard#255), all optional and freely combinable.
    ``source`` is a :class:`chunk_work_refs` existence test (D1) — a chunk may carry
    several work refs, so this is never a join, which would multiply event rows.
    ``extractor_version`` has no default here: mixing versions double-counts the same
    occurrence (D1), so the caller (Phase 3's route) always names one — the current
    :data:`~blizzard.hub.domain.analytics.extraction.EXTRACTOR_VERSION` unless the
    caller asks for an older one explicitly."""

    extractor_version: str
    kind: str | None = None
    tool: str | None = None
    path_prefix: str | None = None
    node_id: str | None = None
    graph_id: str | None = None
    source: str | None = None
    since: datetime | None = None
    until: datetime | None = None


@dataclass(frozen=True)
class EventRecord:
    """One event row as the query layer renders it — the wire layer (Phase 3) shapes
    this further for the two encodings. ``payload`` stays raw JSON object text
    (``bzh:sql-portable``: never parsed or filtered on here)."""

    id: int
    kind: str
    subject: str | None
    tool: str | None
    payload: str
    chunk_id: str
    node_id: str
    epoch: int
    spawn_generation: int
    graph_id: str
    depth: int
    agent_type: str | None
    occurred_at: datetime | None


@dataclass(frozen=True)
class EventPage:
    """A bounded, keyset-paginated page (blizzard#255) — ``next_cursor`` is ``None``
    exactly when this page is the last one under ``criteria``'s ordering, so a caller
    drives a full bulk read by following it until absent."""

    events: list[EventRecord]
    next_cursor: str | None


@dataclass(frozen=True)
class CountRow:
    """One grouping key's count — the four canned aggregations share this shape."""

    key: str
    count: int


class IReadAnalyticsEventQueries(Protocol):
    """Read-only event query Protocol (blizzard#255 D6) — the routes' own seam
    (``bzh:controller-read-only``, ``bzh:repository-split``)."""

    def events(self, criteria: EventQueryCriteria, *, cursor: str | None = None, limit: int = 200) -> EventPage:
        """A page of events matching ``criteria``, ordered by an explicit total order
        (``bzh:sql-portable``) so JSON and NDJSON serve the same ordering and a cursor
        never repeats or skips a row. ``cursor`` continues a prior call's
        :attr:`EventPage.next_cursor`; unset, the read starts from the beginning."""
        ...

    def counts_by_file(self, criteria: EventQueryCriteria) -> list[CountRow]:
        """Occurrence counts grouped by ``subject`` among ``file_read`` events matching
        ``criteria`` — ``criteria.kind`` is honored if it further narrows the scope, but
        this method's own ``file_read`` restriction always applies."""
        ...

    def counts_by_skill(self, criteria: EventQueryCriteria) -> list[CountRow]:
        """Occurrence counts grouped by ``subject`` among ``skill_invocation`` events
        matching ``criteria`` — see :meth:`counts_by_file` for the kind-restriction rule."""
        ...

    def counts_by_agent_type(self, criteria: EventQueryCriteria) -> list[CountRow]:
        """Occurrence counts grouped by the enclosing-sidechain ``agent_type`` column,
        across every kind matching ``criteria`` — "how much activity happened under which
        agent type," not narrowed to ``agent_spawn`` (a caller after spawn counts alone
        gets there with ``criteria.kind="agent_spawn"``, grouped by that kind's own
        ``subject`` via the raw :meth:`events` read)."""
        ...

    def counts_by_node(self, criteria: EventQueryCriteria) -> list[CountRow]:
        """Occurrence counts grouped by ``node_id``, across every kind matching ``criteria``."""
        ...
