"""Project inference serializes with source-link authoring independently of tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import cast
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ingest import IngestProjectAmbiguous, IngestService
from blizzard.hub.domain.chunk.model import IReadWorkItemRepository, WorkRef
from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store, project_link_store
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


class _Labels:
    def source_label(self, source: str) -> str:
        return source

    def label(self, pointer: WorkRef) -> str:
        return f"{pointer.source}:{pointer.ref}"


@pytest.mark.parametrize("omit", [False, True], ids=["decision-lock", "negative-control"])
def test_new_project_link_races_unnamed_ingest(database_url: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "inference_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "inference_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_inference")
    scope = scoped_connections(waiter, tenant)
    holding = scoped_connections(holder, tenant)
    clock = FixedClock(_AT)
    pointer = WorkRef("source", "1")
    try:
        with scope.founding("found", name="inference", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            for name in ("first", "second"):
                conn.execute(
                    scope.insert(s.projects).values(
                        project_id=name, slug=name, name=name, description="", revision=1, color="blue", created_at=_AT
                    )
                )
            conn.execute(
                scope.insert(s.work_sources).values(
                    work_source_id="source",
                    slug="source",
                    name="source",
                    provider="github",
                    locator="a/b",
                    annotate=False,
                    revision=1,
                    created_at=_AT,
                    created_by="test",
                )
            )
            conn.execute(
                scope.insert(s.project_source_links).values(project_id="first", work_source_id="source", revision=1)
            )
            conn.execute(
                scope.insert(s.project_source_link_facts).values(
                    project_id="first", work_source_id="source", linked=True, set_at=_AT, set_by="test"
                )
            )
        stores = build_chunk_stores(scope, clock, registry=RunnerRegistryStore(scope))
        service = IngestService(
            record=stores.record,
            exclusive=stores.exclusive,
            links=ProjectLinkStore(scope),
            items=cast(IReadWorkItemRepository, None),
            labels=_Labels(),
            clock=clock,
        )
        change = ConfigChange(_AT, "test", Door.API, RecordKind.PROJECT_SOURCE_LINK, "source", 1, ChangeOp.CREATE)
        with ThreadPoolExecutor(max_workers=1) as pool, holding.read("author") as conn:
            lock_config_authoring(conn)
            project_link_store.insert_link(
                conn, project_link_store.LINK_KINDS[RecordKind.PROJECT_SOURCE_LINK], "second", "source", change
            )
            with patch.object(
                chunk_exclusive_store, "lock_config_authoring", (lambda conn: None) if omit else lock_config_authoring
            ):
                future = pool.submit(service.ingest, [pointer], graph=make_graph("graph", "g"), project=None)
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name='inference_waiter' AND h.application_name='inference_holder')
                            """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("ingest neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        chunk = stores.record.get(future.result(timeout=5))
                        assert chunk is not None and chunk.project_id == ProjectId("first")
                finally:
                    conn.commit()
                if not omit:
                    with pytest.raises(IngestProjectAmbiguous) as refused:
                        future.result(timeout=10)
                    assert set(refused.value.candidates) == {ProjectId("first"), ProjectId("second")}
                    assert stores.work_refs.find_live_holder(pointer) is None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
