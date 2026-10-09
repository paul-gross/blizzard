"""SQLAlchemy adapter for the hub's own event log (package-private).

``hub_event_log`` is on the global list: its rows concern no tenant, so it is read and written through the hub-level
connections, never a tenant's scoped ones."""

from __future__ import annotations

import json
from collections.abc import Sequence
from datetime import datetime

from sqlalchemy import insert, select

from blizzard.foundation.event_log import EventLogSeverity, HubEventKind, narrow_hub_event_kind
from blizzard.hub.domain.observability.hub_events import HubEvent, HubEventEntry, IWriteHubEventLog
from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreConnections


class HubEventLogStore:
    """Appends and reads ``hub_event_log`` rows."""

    def __init__(self, store: HubStoreConnections) -> None:
        self._store = store

    def newest(self, kinds: Sequence[HubEventKind]) -> HubEvent | None:
        """One read per kind, each a seek through ``ix_hub_event_log_kind_recorded_at`` to that kind's newest row; the
        newest of those wins, a later id breaking a tie as the single-kind order does."""
        c = s.hub_event_log.c
        newest: tuple[datetime, int, HubEvent] | None = None
        with self._store.read("hub_event_log_newest") as conn:
            for kind in kinds:
                row = conn.execute(
                    select(c.id, c.kind, c.recorded_at, c.message)
                    .where(c.kind == kind)
                    .order_by(c.recorded_at.desc(), c.id.desc())
                    .limit(1)
                ).first()
                narrowed = narrow_hub_event_kind(row.kind) if row is not None else None
                if row is None or narrowed is None:
                    continue
                if newest is None or (row.recorded_at, row.id) > newest[:2]:
                    newest = (row.recorded_at, row.id, HubEvent(narrowed, row.recorded_at, row.message))
        return newest[2] if newest is not None else None

    def recent(self, limit: int) -> list[HubEventEntry]:
        """Down ``ix_hub_event_log_recorded_at`` from its newest end, stopped at ``limit``; a later id breaks a tie. A
        row whose kind this build no longer knows is left out."""
        c = s.hub_event_log.c
        with self._store.read("hub_event_log_recent") as conn:
            rows = conn.execute(
                select(c.id, c.severity, c.kind, c.recorded_at, c.message, c.detail)
                .order_by(c.recorded_at.desc(), c.id.desc())
                .limit(limit)
            ).all()
        entries: list[HubEventEntry] = []
        for row in rows:
            kind = narrow_hub_event_kind(row.kind)
            if kind is None:
                continue
            entries.append(
                HubEventEntry(
                    event_id=row.id,
                    severity=EventLogSeverity(row.severity),
                    kind=kind,
                    at=row.recorded_at,
                    message=row.message,
                    detail=json.loads(row.detail) if row.detail is not None else None,
                )
            )
        return entries

    def append(
        self,
        *,
        severity: EventLogSeverity,
        kind: HubEventKind,
        message: str,
        detail: dict | None,  # type: ignore[type-arg]
        at: datetime,
    ) -> int:
        with self._store.write("hub_event_log_record") as conn:
            result = conn.execute(
                insert(s.hub_event_log).values(
                    recorded_at=at,
                    severity=severity,
                    kind=kind,
                    message=message,
                    detail=json.dumps(detail) if detail is not None else None,
                )
            )
            key = result.inserted_primary_key
            return int(key[0]) if key is not None else 0


def _conforms_hub_event_log_store(x: HubEventLogStore) -> IWriteHubEventLog:
    return x
