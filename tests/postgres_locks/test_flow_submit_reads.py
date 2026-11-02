"""Submission inputs are read on the config- and chunk-locked connection."""

from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.chunk.model import CloseEnqueueDecision
from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import PASS, HubActionCatalogue
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import conn_of, lock_chunk_row
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.forge_record_store import ForgeRecordStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.project_link_store import ProjectLinkStore
from blizzard.hub.store.internal.project_store import ProjectStore
from blizzard.hub.store.internal.repository_record_store import RepositoryRecordStore
from tests.postgres_locks.test_project_landing_order import _AT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


def test_locked_refusal_artifact_rolls_back_with_its_transaction(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    try:
        scope, project, _ = _seed(engine)
        with scope.write("seed_chunk") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="gr_1", name="graph", entry_node_id="nd_1", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(
                    chunk_id="ch_1", graph_id="gr_1", project_id=project.project_id, minted_at=_AT
                )
            )
        chunks = build_chunk_stores(scope, SystemClock(), registry=Mock())

        def record(handle):
            return chunks.artifacts.record_hub_artifact_locked(
                handle,
                "ch_1",
                node_id="nd_1",
                node_name="deliver",
                epoch=1,
                admission=EpochAdmission.AT_OR_ABOVE,
                name="hub-log.unresolved",
                content="refused",
                at=_AT,
                close_decision=CloseEnqueueDecision(frozenset(), frozenset()),
            )

        with pytest.raises(RuntimeError, match="abort"), chunks.exclusive.locked_flow_admission(["ch_1"]) as handle:
            assert record(handle)
            assert not record(handle)
            assert [a.name for a in chunks.artifacts.load_artifacts_locked(handle, "ch_1")] == ["hub-log.unresolved"]
            raise RuntimeError("abort")
        assert chunks.artifacts.load_artifacts("ch_1") == []
        with chunks.exclusive.locked_flow_admission(["ch_1"]) as handle:
            assert record(handle)
        assert [a.name for a in chunks.artifacts.load_artifacts("ch_1")] == ["hub-log.unresolved"]
        with chunks.exclusive.locked_flow_admission([]) as handle, pytest.raises(ValueError, match="not locked"):
            record(handle)
    finally:
        engine.dispose()


def test_submission_reads_see_uncommitted_changes_on_locked_connection(database_url: str) -> None:
    engine = sa.create_engine(database_url)
    try:
        scope, project, repos = _seed(engine)
        with scope.write("seed_chunk") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="gr_1", name="graph", entry_node_id="nd_1", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(
                    chunk_id="ch_1", graph_id="gr_1", project_id=project.project_id, minted_at=_AT
                )
            )
        chunks = build_chunk_stores(scope, SystemClock(), registry=Mock())
        with chunks.exclusive.locked_flow_admission(["ch_1"]) as handle:
            conn = conn_of(handle)
            conn.execute(
                scope.update(s.projects).where(s.projects.c.project_id == project.project_id).values(slug="fresh")
            )
            conn.execute(
                scope.update(s.repositories)
                .where(s.repositories.c.repository_id == repos[0].repository_id)
                .values(base_branch="fresh")
            )
            conn.execute(scope.update(s.forges).values(api_base="https://fresh.example"))
            conn.execute(
                scope.insert(s.project_repository_link_facts).values(
                    project_id=project.project_id,
                    repository_id=repos[0].repository_id,
                    linked=False,
                    set_at=_AT,
                    set_by="test",
                )
            )
            conn.execute(
                scope.insert(s.artifacts).values(
                    artifact_id="ar_1",
                    chunk_id="ch_1",
                    node_id="nd_1",
                    node_name="build",
                    epoch=1,
                    name="work",
                    kind="git_commit",
                    data="work:abc",
                    repo="acme/api",
                    produced_at=_AT,
                    seq=1,
                )
            )
            loaded = ProjectStore(scope).get_locked(handle, project.project_id)
            assert loaded is not None and loaded.slug == "fresh"
            assert (
                next(
                    r
                    for r in RepositoryRecordStore(scope).list_all_locked(handle)
                    if r.repository_id == repos[0].repository_id
                ).fields.base_branch
                == "fresh"
            )
            assert ForgeRecordStore(scope).list_all_locked(handle)[0].fields.api_base == "https://fresh.example"
            assert [
                link.repository_id
                for link in ProjectLinkStore(scope).repositories_of_locked(handle, project.project_id)
            ] == [repos[1].repository_id]
            assert [artifact.data for artifact in chunks.artifacts.load_artifacts_locked(handle, "ch_1")] == [
                "work:abc"
            ]
    finally:
        engine.dispose()


def test_reconciler_waits_for_config_before_locking_chunk(database_url: str) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        database_url, connect_args={"application_name": "submit_config_holder", "options": options}
    )
    waiter = sa.create_engine(
        database_url, connect_args={"application_name": "submit_config_waiter", "options": options}
    )
    observer = sa.create_engine(database_url)
    try:
        held, project, _ = _seed(holder)
        waiting = scoped_connections(waiter, held.scope.tenant)
        with held.write("seed_visit") as conn:
            conn.execute(
                held.insert(s.graphs).values(
                    graph_id="gr_1", name="graph", entry_node_id="nd_1", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                held.insert(s.graph_nodes).values(
                    node_id="nd_1",
                    graph_id="gr_1",
                    name="deliver",
                    executor="hub",
                    session="fresh",
                    judged_by="worker",
                    action="pass",
                )
            )
            conn.execute(
                held.insert(s.chunks).values(
                    chunk_id="ch_1", graph_id="gr_1", project_id=project.project_id, minted_at=_AT
                )
            )
            conn.execute(held.insert(s.chunk_promoted).values(chunk_id="ch_1", promoted_at=_AT))
            conn.execute(held.insert(s.lease_facts).values(chunk_id="ch_1", epoch=1, runner_id="runner", minted_at=_AT))
            conn.execute(
                held.insert(s.transitions).values(
                    transition_id="tr_1",
                    chunk_id="ch_1",
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=1,
                    runner_id="runner",
                    recorded_at=_AT,
                )
            )
        clock = SystemClock()
        chunks = build_chunk_stores(waiting, clock, registry=Mock())
        flows = FlowStore(waiting, clock)
        reconciler = FlowLineReconciler(
            flows=flows,
            facts=chunks.facts,
            record=chunks.record,
            graphs=GraphStore(waiting),
            artifacts=chunks.artifacts,
            repositories=RepositoryRecordStore(waiting),
            forge_records=ForgeRecordStore(waiting),
            projects=ProjectStore(waiting),
            links=ProjectLinkStore(waiting),
            catalogue=HubActionCatalogue.of([PASS]),
            refusals=Mock(),
            exclusive=chunks.exclusive,
            lines=Mock(),
            clock=clock,
        )
        with (
            patch.object(waiting, "_lock_open_tenant"),
            held.read("hold_config") as conn,
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            lock_config_authoring(conn)
            future = pool.submit(reconciler.reconcile, 0)
            try:
                deadline = monotonic() + 5
                with observer.connect() as watch:
                    while monotonic() < deadline:
                        watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                        blocked = watch.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                            JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                            WHERE w.application_name = 'submit_config_waiter' AND h.application_name = 'submit_config_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "reconciler bypassed the config lock"
                        Event().wait(0.02)
                    else:
                        pytest.fail("reconciler did not wait for config")
                lock_chunk_row(conn, "ch_1")
            finally:
                conn.commit()
            assert future.result(timeout=10).submitted == 1
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
