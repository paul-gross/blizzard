"""The operational analytics query seam (blizzard#256) — durations, spend, and outcomes
derived at query time (D1, ``bzh:facts-not-status``) over facts the hub already holds:
``transitions``, ``lease_facts``, ``usage_facts``, and ``chunk_migrations``.

New, not an extension of :mod:`queries` (``bzh:controller-read-only``): that module's
``IReadAnalyticsEventQueries`` reads the derived-event projection alone, never a
transition or a usage fact. The routes (Phase 2-4) depend on this Protocol only; no
write repository backs them."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class OperationalCriteria:
    """Every filter the operational datasets owe (blizzard#256 D7) — the scope shared
    with events and counts, narrowed to the four fields that mean something outside the
    derived-event projection: no ``extractor_version``, no event-shape filter."""

    graph_id: str | None = None
    source: str | None = None
    since: datetime | None = None
    until: datetime | None = None
