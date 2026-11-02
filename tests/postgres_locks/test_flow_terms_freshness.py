"""First-turn terms serialize with config writers independently of tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import timedelta
from threading import Event
from time import monotonic
from unittest.mock import Mock, patch

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import SystemClock
from blizzard.hub.domain.delivery.lines import FlowLineFactory
from blizzard.hub.domain.delivery.reconcile import FlowLineReconciler
from blizzard.hub.domain.graph.actions import PASS, HubActionCatalogue
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_exclusive_store
from blizzard.hub.store.internal.chunk_rows import lock_chunk_row
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.config_authoring_lock import lock_config_authoring
from blizzard.hub.store.internal.flow_store import FlowStore
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.postgres_locks.test_flow_decisions import _AT, _EVENT, _RESOURCE, _TENANT, _seed
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("change", ["repository", "position", "unlink"])
@pytest.mark.parametrize("omit", [False, True], ids=["lock", "negative-control"])
def test_admit_captures_config_after_wait(database_url: str, change: str, omit: bool) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "terms_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "terms_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    held, waiting = scoped_connections(holder, _TENANT), scoped_connections(waiter, _TENANT)
    clock = SystemClock()
    stores = build_chunk_stores(waiting, clock, registry=Mock())
    flows, graphs = FlowStore(waiting, clock), GraphStore(waiting)
    read_old, resume = Event(), Event()
    try:
        _seed(held)
        with held.write("seed_terms") as conn:
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
            conn.execute(
                held.insert(s.secrets).values(
                    name="secret",
                    ciphertext="x",
                    nonce="x",
                    key_id="x",
                    revision=1,
                    replaced_at=_AT,
                    replaced_by="test",
                    created_at=_AT,
                )
            )
            conn.execute(
                held.insert(s.repositories).values(
                    repository_id="repo_a",
                    slug="repo",
                    name="repo",
                    forge_api_url="http://example",
                    forge_id="forge",
                    owner="owner",
                    repo="repo",
                    base_branch="main",
                    secret_name="secret",
                    revision=1,
                    created_at=_AT,
                    created_by="test",
                    landing_policy="merge-on-checks",
                )
            )
            conn.execute(
                held.insert(s.project_repository_links).values(project_id="proj_1", repository_id="repo_a", revision=1)
            )
            conn.execute(
                held.insert(s.project_repository_link_facts).values(
                    project_id="proj_1", repository_id="repo_a", linked=True, set_at=_AT, set_by="test"
                )
            )
            conn.execute(
                held.insert(s.project_repository_positions).values(
                    project_id="proj_1", repository_id="repo_a", position=1, set_at=_AT, set_by="test"
                )
            )
        sub = flows.submit("ch_1", node_id="nd_1", epoch=1, generation=0, resources=[_RESOURCE], at=_AT)
        assert sub is not None
        reconciler = FlowLineReconciler(
            flows=flows,
            facts=stores.facts,
            record=stores.record,
            graphs=graphs,
            artifacts=stores.artifacts,
            repositories=Mock(),
            forge_records=Mock(),
            projects=Mock(),
            links=Mock(),
            catalogue=HubActionCatalogue.of([PASS]),
            refusals=Mock(),
            exclusive=stores.exclusive,
            lines=FlowLineFactory(facts=stores.facts, record=stores.record, graphs=graphs),
            clock=clock,
        )
        real_terms = FlowStore.candidate_terms_locked

        def pause_after_read(store, handle, submission, *, chunk=None):
            terms = real_terms(store, handle, submission, chunk=chunk)
            if omit:
                read_old.set()
                assert resume.wait(10), "stale terms read timed out"
            return terms

        real_lock = chunk_exclusive_store.lock_config_authoring

        def selective_lock(conn):
            if not omit:
                real_lock(conn)

        with (
            patch.object(chunk_exclusive_store, "lock_config_authoring", selective_lock),
            patch.object(FlowStore, "candidate_terms_locked", pause_after_read),
            ThreadPoolExecutor(max_workers=1) as pool,
        ):
            with held.read("config_without_admission") as conn:
                lock_config_authoring(conn)
                if change == "repository":
                    conn.execute(
                        held.update(s.repositories)
                        .where(s.repositories.c.repository_id == "repo_a")
                        .values(landing_policy="park", park_timeout_hours=7)
                    )
                elif change == "position":
                    conn.execute(
                        held.insert(s.project_repository_positions).values(
                            project_id="proj_1",
                            repository_id="repo_a",
                            position=9,
                            set_at=_AT + timedelta(seconds=1),
                            set_by="test",
                        )
                    )
                else:
                    conn.execute(
                        held.insert(s.project_repository_link_facts).values(
                            project_id="proj_1",
                            repository_id="repo_a",
                            linked=False,
                            set_at=_AT + timedelta(seconds=1),
                            set_by="test",
                        )
                    )
                future = pool.submit(reconciler.admit, 0, clock.now())
                try:
                    if omit:
                        assert read_old.wait(8), "admission did not read stale terms"
                    else:
                        deadline = monotonic() + 5
                        with observer.connect() as watch:
                            while monotonic() < deadline:
                                watch.execute(sa.text("SELECT pg_stat_clear_snapshot()"))
                                blocked = watch.execute(
                                    sa.text("""
                                    SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                    JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                    WHERE w.application_name = 'terms_waiter' AND h.application_name = 'terms_holder')
                                """)
                                ).scalar_one()
                                if blocked:
                                    break
                                assert not future.done(), "admission bypassed config-authoring"
                                Event().wait(0.02)
                            else:
                                pytest.fail("admission did not wait for config-authoring")
                        # The waiter has not taken the chunk while blocked on configuration.
                        with scoped_connections(observer, _TENANT).read("probe_chunk_order") as probe:
                            lock_chunk_row(probe, "ch_1")
                            probe.rollback()
                    conn.commit()
                finally:
                    resume.set()
            assert len(future.result(timeout=10)) == 1
        term = flows.terms_of(sub.submission_id)["repo_a"]
        expected = (
            ("merge-on-checks", None, 1.0)
            if omit
            else {
                "repository": ("park", 7, 1.0),
                "position": ("merge-on-checks", None, 9.0),
                "unlink": ("merge-on-checks", None, None),
            }[change]
        )
        assert (term.landing_policy, term.park_timeout_hours, term.landing_position) == expected
        turn = flows.open_turns()[0]
        assert flows.end_turn_interrupted(turn.turn_id, withdrawal=None, event=_EVENT, at=clock.now()) is not None
        with held.write("edit_after_first_turn") as conn:
            conn.execute(
                held.update(s.repositories)
                .where(s.repositories.c.repository_id == "repo_a")
                .values(landing_policy="fast-forward", park_timeout_hours=None)
            )
        assert len(reconciler.admit(0, clock.now())) == 1
        assert flows.terms_of(sub.submission_id)["repo_a"] == term
    finally:
        resume.set()
        holder.dispose()
        waiter.dispose()
        observer.dispose()
