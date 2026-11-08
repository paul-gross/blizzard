"""``ChunkEventsStore.list_events`` severity filter (component tier): it matches the severity a row
displays — a recognized stored severity, else its recognized kind's declared severity, else critical —
and applies before the result limit."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.event_log import EventLogSeverity
from blizzard.hub.store.internal.chunk_events_store import ChunkEventsStore
from tests.support.scaffolding import hub_store_connections, migrated_hub_at

_T0 = datetime(2026, 1, 1, tzinfo=UTC)

_CRITICAL_KIND = "needs-human"  # declared critical in EVENT_LOG_SEVERITY
_INFO_KIND = "flow-landed"


def _store(tmp_path: Path) -> ChunkEventsStore:
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migrated_hub_at(db_url)
    engine = sa.create_engine(db_url)
    return ChunkEventsStore(hub_store_connections(engine), FixedClock(_T0))


def _record(store: ChunkEventsStore, *, severity: str, kind: str, at: int) -> int:
    return store.record_event(
        severity=cast(Any, severity),
        kind=kind,
        runner_id=None,
        chunk_id=None,
        lease_id=None,
        node_name=None,
        message="m",
        detail=None,
        at=_T0 + timedelta(seconds=at),
    )


def _ids(store: ChunkEventsStore, severity: EventLogSeverity, **kw: Any) -> list[int]:
    return [e.id for e in store.list_events(severity=severity, **kw)]


def test_obsolete_severity_with_known_kind_filters_by_kind_severity(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = _record(store, severity="error", kind=_CRITICAL_KIND, at=1)

    assert _ids(store, EventLogSeverity.CRITICAL) == [row]
    assert _ids(store, EventLogSeverity.INFO) == []
    assert store.list_events()[0].severity is EventLogSeverity.CRITICAL


def test_obsolete_severity_with_unknown_kind_is_critical(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = _record(store, severity="error", kind="no-such-kind", at=1)

    assert _ids(store, EventLogSeverity.CRITICAL) == [row]
    assert _ids(store, EventLogSeverity.WARNING) == []


def test_recognized_severity_wins_over_kind_mapping(tmp_path: Path) -> None:
    store = _store(tmp_path)
    row = _record(store, severity="info", kind=_CRITICAL_KIND, at=1)

    assert _ids(store, EventLogSeverity.INFO) == [row]
    assert _ids(store, EventLogSeverity.CRITICAL) == []


def test_limit_applies_after_effective_severity_filter(tmp_path: Path) -> None:
    store = _store(tmp_path)
    old_match = _record(store, severity="error", kind=_CRITICAL_KIND, at=1)
    for i in range(2, 5):
        _record(store, severity="info", kind=_INFO_KIND, at=i)

    assert _ids(store, EventLogSeverity.CRITICAL, limit=1) == [old_match]
