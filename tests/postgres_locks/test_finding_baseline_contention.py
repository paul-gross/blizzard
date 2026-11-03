"""Each baseline participant acquires the key without relying on tenant admission."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from blizzard.hub.domain.chunk.ports.fence import EpochAdmission
from blizzard.hub.domain.garden.delivery.materialize import (
    DeliveryOutcome,
    DeliveryPlan,
    DeltaMaterialization,
    NewFindingSet,
)
from blizzard.hub.domain.garden.run_context import RunContext
from blizzard.hub.store import routine_run_participants
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal import finding_store, garden_delivery_store
from blizzard.hub.store.internal.finding_set_lock import lock_finding_set_baselines
from blizzard.hub.store.internal.finding_store import FindingSetStore
from blizzard.hub.store.internal.garden_delivery_store import GardenDeliveryStore
from blizzard.hub.tenancy import TenantId
from tests.postgres_locks.test_config_apply_unit import _wait_for_key
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock
_AT = datetime(2026, 10, 29, tzinfo=UTC)
_KEY = ("project", "nightly", "scope")


@pytest.mark.parametrize("actor", ["reader", "create", "delivery"])
@pytest.mark.parametrize("omit_lock", [False, True], ids=["locked", "omitted"])
def test_baseline_participants_contend_on_the_same_key(database_url, monkeypatch, actor, omit_lock):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "apply_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "apply_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    try:
        tenant = TenantId("ten_baseline")
        scope = scoped_connections(holder, tenant)
        other = scoped_connections(waiter, tenant)
        with scope.founding("found", name="baseline", created_at=_AT, created_by="test"):
            pass
        with scope.write("seed") as conn:
            conn.execute(
                scope.insert(s.projects).values(
                    project_id="project", slug="p", name="p", description="", revision=1, color="blue", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.graphs).values(
                    graph_id="graph", name="g", entry_node_id="n", definition_yaml="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.chunks).values(chunk_id="chunk", graph_id="graph", project_id="project", minted_at=_AT)
            )
            conn.execute(
                scope.insert(s.scopes).values(
                    scope_id="scope_id", project_id="project", slug="scope", description="", created_at=_AT
                )
            )
            conn.execute(
                scope.insert(s.artifacts).values(
                    artifact_id="artifact",
                    chunk_id="chunk",
                    node_id="node",
                    node_name="garden",
                    epoch=1,
                    name="delta",
                    kind="asset",
                    data="{}",
                    produced_at=_AT,
                    seq=1,
                )
            )

        modules = {"reader": routine_run_participants, "create": finding_store, "delivery": garden_delivery_store}
        if omit_lock:
            monkeypatch.setattr(modules[actor], "lock_finding_set_baselines", lambda conn, pairs: None)

        def act():
            if actor == "reader":
                # No write admission: only the baseline key can block this read.
                with other.read("baseline") as conn:
                    return routine_run_participants._AdmittedFindingSets(
                        other.borrow(conn), conn
                    ).newest_for_routine_scope(*_KEY)
            if actor == "create":
                return FindingSetStore(other).create(
                    "set_new",
                    artifact_id="artifact",
                    chunk_id="chunk",
                    project_id="project",
                    scope_slug="scope",
                    routine_name="nightly",
                    revisions={},
                    measurement=None,
                )
            plan = DeliveryPlan(
                "chunk",
                "node2",
                "garden",
                1,
                _AT,
                RunContext(project_id="project", routine_name="nightly", scope_slug="scope", mode="dry_run"),
                [DeltaMaterialization(NewFindingSet("set_new", "artifact", "scope", {}, None))],
                [],
                {},
            )
            return GardenDeliveryStore(other).deliver(plan, admission=EpochAdmission.AT_OR_ABOVE)

        with ThreadPoolExecutor(max_workers=1) as pool, scope.read("hold baseline") as conn:
            lock_finding_set_baselines(conn, [_KEY])
            if actor == "reader":
                conn.execute(
                    scope.insert(s.finding_sets).values(
                        finding_set_id="set_new",
                        artifact_id="artifact",
                        chunk_id="chunk",
                        project_id="project",
                        scope_id="scope_id",
                        scope_slug="scope",
                        routine_name="nightly",
                        revisions="{}",
                    )
                )
            future = pool.submit(act)
            if omit_lock:
                result = future.result(timeout=5)
                if actor == "reader":
                    assert result is None
                elif actor == "delivery":
                    assert result is DeliveryOutcome.RECORDED
                else:
                    assert result.finding_set_id == "set_new"
            else:
                _wait_for_key(observer, future)
            conn.commit()
            if not omit_lock:
                result = future.result(timeout=10)
                if actor == "delivery":
                    assert result is DeliveryOutcome.RECORDED
                else:
                    assert result.finding_set_id == "set_new"
        assert FindingSetStore(scope).newest_for_routine_scope(*_KEY).finding_set_id == "set_new"
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
