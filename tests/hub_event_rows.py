"""The hub's own event log, as tests write and read it — its store and service over a migrated engine, and its rows."""

from __future__ import annotations

import json
from typing import Any

import sqlalchemy as sa
from sqlalchemy import Engine

from blizzard.hub.domain.observability.hub_events import HubEventLogService
from blizzard.hub.store import schema
from blizzard.hub.store.internal.hub_event_log_store import HubEventLogStore
from tests.support import hub_level_connections


def hub_events_of(engine: Engine) -> HubEventLogStore:
    return HubEventLogStore(hub_level_connections(engine))


def hub_event_log_of(engine: Engine) -> HubEventLogService:
    return HubEventLogService(events=hub_events_of(engine))


def hub_event_rows(engine: Engine, *kinds: str) -> list[sa.Row[Any]]:
    """Every ``hub_event_log`` row, oldest first — only those of ``kinds`` when any are named."""
    h = schema.hub_event_log.c
    stmt = sa.select(schema.hub_event_log).order_by(h.id)
    if kinds:
        stmt = stmt.where(h.kind.in_(kinds))
    with engine.connect() as conn:
        return list(conn.execute(stmt).all())


def hub_event_kinds(engine: Engine, prefix: str = "") -> list[str]:
    """The kinds of ``hub_event_log``'s rows, oldest first — only those starting with ``prefix`` when one is given."""
    return [row.kind for row in hub_event_rows(engine) if row.kind.startswith(prefix)]


def hub_event_detail(row: sa.Row[Any]) -> dict[str, Any]:
    return json.loads(row.detail) if isinstance(row.detail, str) else row.detail
