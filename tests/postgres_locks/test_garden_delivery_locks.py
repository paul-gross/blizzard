"""Delivery locks substituted citations before higher finding IDs and FK checks."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa
from sqlalchemy import event
from sqlalchemy.exc import OperationalError

from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeltaMaterialization,
    NewFinding,
    NewFindingFact,
    NewFindingSet,
    NewProposal,
)
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import garden_delivery_store
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize("omit_citation_lock", [False, True])
def test_substituted_citation_locks_before_guard_and_fk(
    database_url: str, omit_citation_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "garden_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "garden_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    now = datetime.now(UTC)
    tenant = TenantId("ten_garden_locks")
    link_insert = Event()

    @event.listens_for(waiter, "before_cursor_execute")
    def track_link(conn, cursor, statement, parameters, context, executemany):
        if statement.lower().startswith("insert into garden_proposal_findings"):
            link_insert.set()

    try:
        scope = scoped_connections(holder, tenant)
        with scope.founding("found", name="garden", created_at=now, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=now
                )
            )
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=now
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=now)
            )
            conn.execute(
                scope.insert(s.scopes).values(
                    scope_id="scope", project_id="project", slug="scope", description="", created_at=now
                )
            )
            for artifact_id, seq in [("art_delta", 1), ("art_new", 2), ("art_proposal", 3)]:
                conn.execute(
                    scope.insert(s.artifacts).values(
                        artifact_id=artifact_id,
                        chunk_id="chunk",
                        node_id="node",
                        node_name="garden",
                        epoch=1,
                        name=artifact_id,
                        kind="asset",
                        data="{}",
                        produced_at=now,
                        seq=seq,
                    )
                )
        findings = FindingStore(scope)
        for fid in ("fin_a", "fin_z"):
            findings.add(
                fid,
                project_id="project",
                routine_name="nightly",
                scope_slug="scope",
                class_="c",
                locus=fid,
                summary="s",
                introduced=None,
                at=now,
            )
        # An earlier delivery recorded this artifact's add/ref as fin_a.
        with scope.write("seed_set") as conn:
            conn.execute(
                scope.insert(s.finding_sets).values(
                    finding_set_id="set_old",
                    artifact_id="art_delta",
                    chunk_id="chunk",
                    project_id="project",
                    scope_id="scope",
                    scope_slug="scope",
                    routine_name="nightly",
                    revisions="{}",
                )
            )
            conn.execute(
                scope.insert(s.finding_facts).values(
                    finding_id="fin_a", kind="add", recorded_at=now, finding_set_id="set_old", ref="F1"
                )
            )
        new = NewFinding("fin_phantom", "nightly", "scope", "c", "a", "s", None, None)
        old_delta = DeltaMaterialization(
            NewFindingSet("set_replay", "art_delta", "scope", {}, None),
            [new],
            [NewFindingFact("fin_phantom", "add", "set_replay", ref="F1")],
        )
        new_delta = DeltaMaterialization(
            NewFindingSet("set_new", "art_new", "scope", {}, None),
            facts=[NewFindingFact("fin_z", "observed", "set_new")],
        )
        proposal = NewProposal(
            "proposal", "nightly", "fix-the-source", "title", "body", "art_proposal", "p1", ["fin_phantom"]
        )
        plan = DeliveryPlan(
            "chunk",
            "node2",
            "garden",
            1,
            now,
            RunContext(project_id="project", routine_name="nightly", scope_slug="scope", mode="dry_run"),
            [old_delta, new_delta],
            [proposal],
            {"fin_z": "live"},
        )
        store = GardenDeliveryStore(scoped_connections(waiter, tenant))
        if omit_citation_lock:
            original = garden_delivery_store.lock_findings
            monkeypatch.setattr(
                garden_delivery_store,
                "lock_findings",
                lambda conn, ids: original(conn, [fid for fid in ids if fid != "fin_a"]),
            )

        with ThreadPoolExecutor(max_workers=1) as actors:
            # An ordinary non-key update holds NO KEY UPDATE, compatible with FK KEY SHARE.
            with scope.read("hold_finding") as conn:
                conn.execute(sa.text("UPDATE findings SET summary = summary WHERE finding_id = 'fin_a'"))
                future = actors.submit(store.deliver, plan, admission=EpochAdmission.AT_OR_ABOVE)
                if omit_citation_lock:
                    assert future.result(timeout=5) is DeliveryOutcome.RECORDED
                    assert link_insert.is_set()
                else:
                    deadline = monotonic() + 5
                    with observer.connect() as monitor:
                        while monotonic() < deadline:
                            blocked = monitor.execute(
                                sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                              JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                              WHERE w.application_name = 'garden_waiter' AND h.application_name = 'garden_holder')
                        """)
                            ).scalar_one()
                            if blocked:
                                break
                            assert not future.done(), "delivery escaped the held finding"
                            Event().wait(0.02)
                        else:
                            pytest.fail("delivery did not block on the finding")
                assert link_insert.is_set() is omit_citation_lock
                # The blocked delivery must not already hold the higher finding.
                savepoint = conn.begin_nested()
                try:
                    conn.execute(
                        sa.text("SELECT finding_id FROM findings WHERE finding_id = 'fin_z' FOR UPDATE NOWAIT")
                    )
                except OperationalError as exc:
                    pytest.fail(f"delivery held the higher finding while waiting: {exc}")
                finally:
                    savepoint.rollback()
            assert future.result(timeout=10) is DeliveryOutcome.RECORDED
        with scope.read("verify") as conn:
            assert (
                conn.execute(
                    scope.select(s.garden_proposal_findings, s.garden_proposal_findings.c.finding_id).where(
                        s.garden_proposal_findings.c.proposal_id == "proposal"
                    )
                ).scalar_one()
                == "fin_a"
            )
            assert (
                conn.execute(
                    scope.select(s.finding_facts, s.finding_facts.c.kind).where(
                        s.finding_facts.c.finding_id == "fin_z", s.finding_facts.c.kind == "observed"
                    )
                ).scalar_one()
                == "observed"
            )
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
