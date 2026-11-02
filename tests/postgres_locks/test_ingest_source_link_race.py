"""Ingest rechecks project source links under the PostgreSQL decision key."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from typing import cast
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.domain.chunk.ingest import IngestService, IngestSourceNotLinked
from blizzard.hub.domain.chunk.model import IReadWorkItemRepository, WorkRef
from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.domain.config.work_sources import WorkSourceId
from blizzard.hub.domain.projects.model import ProjectId
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store, project_link_store
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.project_link_store import PROJECT_SOURCE_LINKS_LOCK_NAMESPACE, ProjectLinkStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)
_PROJECT = ProjectId("project")
_POINTER = WorkRef("source", "1")


class _Labels:
    def source_label(self, source: str) -> str:
        return source

    def label(self, pointer: WorkRef) -> str:
        return f"{pointer.source}:{pointer.ref}"


@pytest.mark.parametrize("commit", [True, False], ids=["unlink-commits", "unlink-rolls-back"])
@pytest.mark.parametrize("omit", [False, True], ids=["locked", "negative-control"])
@pytest.mark.parametrize("relink", [False, True], ids=["unlink", "unlink-relink"])
def test_ingest_against_unlink(database_url: str, commit: bool, omit: bool, relink: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "source_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "source_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_source_race")
    scope = scoped_connections(waiter, tenant)
    holding = scoped_connections(holder, tenant)
    clock = FixedClock(_AT)
    try:
        with scope.founding("found", name="source race", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id=_PROJECT, slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
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
                scope.insert(s.project_source_links).values(project_id=_PROJECT, work_source_id="source", revision=1)
            )
            conn.execute(
                scope.insert(s.project_source_link_facts).values(
                    project_id=_PROJECT, work_source_id="source", linked=True, set_at=_AT, set_by="test"
                )
            )
        stores = build_chunk_stores(scope, clock, registry=RunnerRegistryStore(scope))
        links = ProjectLinkStore(scope)
        service = IngestService(
            record=stores.record,
            exclusive=stores.exclusive,
            links=links,
            items=cast(IReadWorkItemRepository, None),
            labels=_Labels(),
            clock=clock,
        )
        change = ConfigChange(_AT, "test", Door.API, RecordKind.PROJECT_SOURCE_LINK, "source", 2, ChangeOp.RETIRE)

        def selective_lock(conn: sa.Connection, namespace: str, keys: list[str]) -> None:
            if not (omit and namespace == PROJECT_SOURCE_LINKS_LOCK_NAMESPACE):
                lock_keys(conn, namespace, keys)

        chunk_id: str | None = None
        with ThreadPoolExecutor(max_workers=1) as pool, holding.read("unlink") as conn:
            project_link_store.record_link(
                conn,
                project_link_store.LINK_KINDS[RecordKind.PROJECT_SOURCE_LINK],
                _PROJECT,
                "source",
                linked=False,
                revision=2,
                from_revision=1,
                change=change,
            )
            if relink:
                enabled = ConfigChange(
                    _AT, "test", Door.API, RecordKind.PROJECT_SOURCE_LINK, "source", 3, ChangeOp.ENABLE
                )
                project_link_store.record_link(
                    conn,
                    project_link_store.LINK_KINDS[RecordKind.PROJECT_SOURCE_LINK],
                    _PROJECT,
                    "source",
                    linked=True,
                    revision=3,
                    from_revision=2,
                    change=enabled,
                )
            with patch.object(chunk_exclusive_store, "lock_keys", selective_lock):
                future = pool.submit(service.ingest, [_POINTER], graph=make_graph("graph", "g"), project=_PROJECT)
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            blocked = watch.execute(
                                sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                    JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                    WHERE w.application_name='source_waiter' AND h.application_name='source_holder')
                                """)
                            ).scalar_one()
                            if blocked or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("ingest neither blocked nor completed")
                    assert blocked is not omit
                    if omit:
                        chunk_id = future.result(timeout=5)
                finally:
                    if commit:
                        conn.commit()
                    else:
                        conn.rollback()
                if not omit:
                    if commit and not relink:
                        with pytest.raises(IngestSourceNotLinked) as refusal:
                            future.result(timeout=10)
                        assert (refusal.value.source, refusal.value.project) == ("source", _PROJECT)
                    else:
                        chunk_id = future.result(timeout=10)
        link = links.source_link(_PROJECT, WorkSourceId("source"))
        assert link is not None and link.linked == (not commit or relink)
        if not omit and commit and not relink:
            assert stores.work_refs.find_live_holder(_POINTER) is None
        else:
            assert chunk_id is not None
            chunk = stores.record.get(chunk_id)
            assert chunk is not None and chunk.project_id == _PROJECT and chunk.work_refs == [_POINTER]
            assert stores.work_refs.find_live_holder(_POINTER) == chunk_id
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
