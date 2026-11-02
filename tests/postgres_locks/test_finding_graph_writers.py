"""Direct writers participate in the same locks as guarded garden and default mint."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.store.internal.finding_store as finding_module
import blizzard.hub.store.internal.graph_store as graph_module
from blizzard.hub.domain.garden.findings.model import FactEntry
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.finding_store import FindingStore, lock_findings
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.tenancy import TenantId
from tests.support import make_graph, scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("writer", ["fact", "fact_guarded_extra", "graph"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_direct_writer_waits_for_decision_lock(
    database_url: str, writer: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "direct_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "direct_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    tenant = TenantId("ten_direct_lock")
    now = datetime.now(UTC)
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="direct-lock", created_at=now, created_by="test"):
            pass
        with scoped.write("seed") as conn:
            conn.execute(
                scoped.insert(s.projects).values(
                    project_id="prj_1", slug="p", name="p", description="", revision=1, created_at=now, color="teal"
                )
            )
            conn.execute(
                scoped.insert(s.scopes).values(
                    scope_id="scp_1", project_id="prj_1", slug="s", description="", created_at=now
                )
            )
        store = FindingStore(scoped_connections(waiter, tenant))
        for fid in ("fin_a", "fin_b"):
            store.add(
                fid,
                project_id="prj_1",
                routine_name="r",
                scope_slug="s",
                class_="c",
                locus="l",
                summary="s",
                introduced=None,
                at=now,
            )
        graphs = GraphStore(scoped_connections(waiter, tenant))

        def act() -> None:
            if writer == "graph":
                graphs.mint(make_graph("gr_1", "g"), definition_yaml="", at=now)
            else:
                expect = {"fin_a": "live"} if writer == "fact_guarded_extra" else None
                assert (
                    store.record_facts(
                        [FactEntry(finding_id="fin_b", kind="observed", at=now, note=None)], expect=expect
                    )
                    == []
                )

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_decision") as conn:
                if writer == "graph":
                    lock_keys(conn, "graph_name", ["g"])
                    if omit_lock:
                        monkeypatch.setattr(graph_module, "lock_keys", lambda *args: None)
                else:
                    lock_findings(conn, ["fin_b"])
                    if omit_lock:
                        monkeypatch.setattr(finding_module, "lock_findings", lambda *args: None)
                future = actors.submit(act)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                  WHERE w.application_name = 'direct_waiter' AND h.application_name = 'direct_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "writer completed without its decision lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("writer did not block on the decision lock")
            future.result(timeout=10)
        if writer == "graph":
            assert graphs.get("gr_1") is not None
        else:
            finding = store.get("fin_b")
            assert finding is not None
            assert finding.observed_count == 1
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
