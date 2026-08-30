"""The hub-store error-wrapping seam (blizzard#413) — a driver fault through every one
of the 13 ``hub/store/internal/`` adapters raises the wrapped ``HubStoreError``, logged
once at the collaborator's single wrap site (D1, D4). One parametrized case drives all
13 (D6, ``bzh:case-pins-its-own-name``) rather than 13 near-identical copies."""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import Engine
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.analytics.operational import OperationalCriteria
from blizzard.hub.domain.analytics.queries import EventQueryCriteria
from blizzard.hub.runtime import migration_runner
from blizzard.hub.store.errors import HubStoreError
from blizzard.hub.store.internal.analytics_event_query_store import AnalyticsEventQueryStore
from blizzard.hub.store.internal.analytics_operational_store import AnalyticsOperationalStore
from blizzard.hub.store.internal.chunk_store import ChunkStore
from blizzard.hub.store.internal.finding_store import FindingSetStore, FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.transcript_event_store import TranscriptEventStore
from blizzard.hub.store.internal.transcript_segment_store import TranscriptSegmentStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import hub_store_connections

pytestmark = pytest.mark.component

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


@dataclass(frozen=True)
class _AdapterCase:
    name: str
    build: Callable[[Any], Any]
    drive: Callable[[Any], None]
    operation: str


_ADAPTER_CASES = [
    _AdapterCase("ScopeStore", lambda store: ScopeStore(store), lambda a: a.get("x"), "get"),
    _AdapterCase(
        "AnalyticsEventQueryStore",
        lambda store: AnalyticsEventQueryStore(store),
        lambda a: a.counts_by_file(EventQueryCriteria(extractor_version="v1")),
        "counts",
    ),
    _AdapterCase(
        "AnalyticsOperationalStore",
        lambda store: AnalyticsOperationalStore(store),
        lambda a: a.durations_by_node(OperationalCriteria()),
        "step_durations",
    ),
    _AdapterCase("ChunkStore", lambda store: ChunkStore(store, FixedClock(_NOW)), lambda a: a.get("ch_x"), "get"),
    _AdapterCase("FindingStore", lambda store: FindingStore(store), lambda a: a.get("fin_x"), "get"),
    _AdapterCase("FindingSetStore", lambda store: FindingSetStore(store), lambda a: a.get("fins_x"), "get"),
    _AdapterCase("GardenProposalStore", lambda store: GardenProposalStore(store), lambda a: a.get("gp_x"), "get"),
    _AdapterCase("GraphStore", lambda store: GraphStore(store), lambda a: a.get("gr_x"), "get"),
    _AdapterCase("RoutineStore", lambda store: RoutineStore(store), lambda a: a.get("rtn_x"), "get"),
    _AdapterCase(
        "RunnerRegistryStore",
        lambda store: RunnerRegistryStore(store),
        lambda a: a.get_runner("runner-x"),
        "get_runner",
    ),
    _AdapterCase(
        "TranscriptEventStore",
        lambda store: TranscriptEventStore(store),
        lambda a: a.visible_segment_ids(),
        "visible_segment_ids",
    ),
    _AdapterCase(
        "TranscriptSegmentStore",
        lambda store: TranscriptSegmentStore(store),
        lambda a: a.high_water("runner-x"),
        "high_water",
    ),
    _AdapterCase("WorkItemStore", lambda store: WorkItemStore(store), lambda a: a.get("hub", "1"), "get"),
]


def _schema_missing_engine(tmp_path: Path) -> Engine:
    """A migrated engine whose schema then vanishes from under it — every one of the
    13 adapters' methods below queries a table that no longer exists, a genuine driver
    fault raised mid-query rather than at connection acquisition (the
    ``test_scope_store.py`` pilot's own technique, reused here across every adapter)."""
    db_url = f"sqlite:///{tmp_path / 'hub.db'}"
    migration_runner(HubConfig(root=tmp_path, db_url=db_url)).upgrade("head")
    engine = create_engine_from_url(db_url)
    engine.dispose()
    (tmp_path / "hub.db").unlink()
    return engine


@pytest.mark.parametrize("case", _ADAPTER_CASES, ids=lambda c: c.name)
def test_a_driver_fault_through_each_adapter_raises_the_wrapped_error_and_logs_once(
    case: _AdapterCase, tmp_path: Path
) -> None:
    adapter = case.build(hub_store_connections(_schema_missing_engine(tmp_path)))

    with capture_logs() as logs, pytest.raises(HubStoreError) as exc_info:
        case.drive(adapter)

    assert exc_info.value.operation == case.operation
    error_logs = [entry for entry in logs if entry["log_level"] == "error"]
    assert len(error_logs) == 1
    assert error_logs[0]["operation"] == case.operation
