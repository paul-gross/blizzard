"""Resolution's first-write-wins guard is protected independently of tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Barrier, Event
from time import monotonic

import pytest
import sqlalchemy as sa

from blizzard.foundation.clock import FixedClock
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import chunk_decisions_store
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 1, 1, tzinfo=UTC)


@pytest.mark.parametrize("scenario", ["lock", "omission", "winner"])
def test_resolution_decision_lock(database_url: str, scenario: str, monkeypatch: pytest.MonkeyPatch) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "decision_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "decision_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    scope = scoped_connections(holder, TenantId("ten_decision_lock"))
    try:
        with scope.founding("found", name="decision-lock", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=_AT)
            )
            conn.execute(
                scope.insert(s.decisions).values(
                    decision_id="decision",
                    chunk_id="chunk",
                    project_id="project",
                    node_id="n",
                    node_name="n",
                    epoch=1,
                    choices="[]",
                    check_results="[]",
                    submitted_at=_AT,
                )
            )
        store = chunk_decisions_store.ChunkDecisionsStore(
            scoped_connections(waiter, TenantId("ten_decision_lock")), FixedClock(_AT)
        )
        if scenario == "omission":
            monkeypatch.setattr(chunk_decisions_store, "lock_decision_row", lambda *args: None)

        with ThreadPoolExecutor(max_workers=1) as pool:
            # A read transaction deliberately holds only the decision lock, not admission.
            with scope.read("hold_decision") as conn:
                chunk_decisions_store_lock = (
                    scope.update(s.decisions)
                    .where(s.decisions.c.decision_id == "decision")
                    .values(submitted_at=s.decisions.c.submitted_at)
                )
                conn.execute(chunk_decisions_store_lock)
                if scenario == "winner":
                    conn.execute(
                        scope.insert(s.decision_resolutions).values(
                            decision_id="decision",
                            choice="winner",
                            resolved_by="first",
                            resolved_at=_AT,
                            overrode_red_checks=True,
                            note="kept",
                        )
                    )
                future = pool.submit(
                    store.record_decision_resolution, "decision", choice="loser", resolved_by="second", at=_AT
                )
                if scenario == "omission":
                    assert future.result(timeout=5) is True
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                                JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                                WHERE w.application_name = 'decision_waiter' AND h.application_name = 'decision_holder')
                            """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "resolution bypassed its decision lock"
                            Event().wait(0.02)
                        else:
                            pytest.fail("resolution did not block on the decision")
                    conn.commit()
            assert future.result(timeout=10) is (scenario != "winner")
        decision = store.get_decision("decision")
        assert decision is not None
        assert (decision.resolved_choice, decision.resolved_by, decision.overrode_red_checks, decision.note) == (
            ("winner", "first", True, "kept") if scenario == "winner" else ("loser", "second", False, None)
        )
        assert store.record_decision_resolution("decision", choice="later", resolved_by="third", at=_AT) is False
        if scenario == "lock":
            with scope.write("seed_race") as conn:
                conn.execute(
                    scope.insert(s.decisions).values(
                        decision_id="race",
                        chunk_id="chunk",
                        project_id="project",
                        node_id="n",
                        node_name="n",
                        epoch=2,
                        choices="[]",
                        check_results="[]",
                        submitted_at=_AT,
                    )
                )
            barrier = Barrier(8)

            def resolve(index: int) -> bool:
                barrier.wait(timeout=5)
                return store.record_decision_resolution("race", choice=str(index), resolved_by=str(index), at=_AT)

            with ThreadPoolExecutor(max_workers=8) as pool:
                outcomes = list(pool.map(resolve, range(8)))
            assert outcomes.count(True) == 1
            winner = str(outcomes.index(True))
            decision = store.get_decision("race")
            assert decision is not None
            assert (decision.resolved_choice, decision.resolved_by) == (winner, winner)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
