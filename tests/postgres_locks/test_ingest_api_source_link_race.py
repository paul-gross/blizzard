"""The ingest HTTP transaction infers its project after concurrent link authoring."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic
from unittest.mock import patch

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.config.changes import ChangeOp, ConfigChange, Door, RecordKind
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store, project_link_store
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from tests.support import FakeWorkSource, build_hub, default_project, default_tenant, scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("commit", [True, False], ids=["link-commits", "link-rolls-back"])
@pytest.mark.parametrize("omit", [False, True], ids=["locked", "negative-control"])
def test_api_infers_after_concurrent_link(database_url, tmp_path, commit, omit):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "api_link_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "api_link_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    scoped = scoped_connections(holder, default_tenant(holder))
    first = default_project(holder)
    try:
        with scoped.write("seed") as conn:
            conn.execute(
                scoped.insert(s.projects).values(
                    project_id="second",
                    slug="second",
                    name="second",
                    description="",
                    revision=1,
                    color="blue",
                    created_at=_AT,
                )
            )
            conn.execute(
                scoped.insert(s.work_sources).values(
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
            for project, linked in ((first, True), ("second", False)):
                conn.execute(
                    scoped.insert(s.project_source_links).values(
                        project_id=project, work_source_id="source", revision=1
                    )
                )
                conn.execute(
                    scoped.insert(s.project_source_link_facts).values(
                        project_id=project, work_source_id="source", linked=linked, set_at=_AT, set_by="test"
                    )
                )
        # Reuse the real HTTP composition, but bind its stores to this test's migrated PostgreSQL database.
        hub = build_hub(
            tmp_path, engine=waiter, work_sources={"source": FakeWorkSource(name="source")}, link=False, repositories=[]
        )
        change = ConfigChange(_AT, "test", Door.API, RecordKind.PROJECT_SOURCE_LINK, "source", 2, ChangeOp.ENABLE)
        with ThreadPoolExecutor(max_workers=1) as pool, scoped.read("change_link_without_admission") as conn:
            lock_config_authoring(conn)
            project_link_store.record_link(
                conn,
                project_link_store.LINK_KINDS[RecordKind.PROJECT_SOURCE_LINK],
                "second",
                "source",
                linked=True,
                revision=2,
                from_revision=1,
                change=change,
            )
            with patch.object(
                chunk_exclusive_store, "lock_config_authoring", (lambda conn: None) if omit else lock_config_authoring
            ):
                future = pool.submit(hub.client.post, "/api/chunks", json={"tokens": ["source:1"]})
                try:
                    deadline = monotonic() + 5
                    with observer.connect() as watch:
                        while monotonic() < deadline:
                            query = watch.execute(
                                sa.text("""
                                SELECT w.query FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name='api_link_waiter' AND h.application_name='api_link_holder'
                            """)
                            ).scalar_one_or_none()
                            if query is not None or future.done():
                                break
                            Event().wait(0.02)
                        else:
                            pytest.fail("API ingest neither blocked nor completed")
                    if omit:
                        assert query is None
                        future.result(timeout=5)
                    else:
                        assert query is not None and "keyed_locks" in query.lower()
                finally:
                    if commit:
                        conn.commit()
                    else:
                        conn.rollback()
                response = future.result(timeout=10)
        body = response.json()
        with scoped.read("verify_link") as conn:
            linked = project_link_store.projects_linking_sources_conn(conn, ["source"])["source"]
        assert set(linked) == ({first, "second"} if commit else {first})
        if commit and not omit:
            assert response.status_code == 422, body
            assert body["detail"]["code"] == "project_required"
            assert {project["project_id"] for project in body["detail"]["projects"]} == {first, "second"}
            assert hub.services.graphs.get_enabled_by_name(hub.services.default_graph_doc.name) is None
            assert hub.client.get("/api/chunks").json()["chunks"] == []
        else:
            assert response.status_code == 201, body
            assert body["project_id"] == first
            chunk = hub.services.chunks.record.get(body["chunk_id"])
            assert chunk is not None and chunk.project_id == first
            assert [(ref.source, ref.ref) for ref in chunk.work_refs] == [("source", "1")]
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
