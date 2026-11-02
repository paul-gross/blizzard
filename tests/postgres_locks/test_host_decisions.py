"""Host decisions queue on their own keys, not on the tenant admission lock."""

import json
from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.store.internal.host_registry_store as host_module
from blizzard.hub.domain.runners.host import (
    HostAddition,
    HostLifecycleFact,
    HostNameTaken,
    HostReport,
    HostTokenRotation,
)
from blizzard.hub.domain.runners.registration import RunnerAddition
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


@pytest.mark.parametrize(
    ("operation", "omit_lock"),
    [
        (name, False)
        for name in (
            "add",
            "report",
            "adopt",
            "retire",
            "enroll",
            "runner-add",
            "runner-retire",
            "sample",
            "miss",
            "water",
        )
    ]
    + [("add", True), ("sample", True), ("miss", True), ("water", True)],
)
def test_host_decision_waits_without_holder_tenant_admission(
    database_url: str, operation: str, omit_lock: bool, monkeypatch: pytest.MonkeyPatch
) -> None:
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(
        database_url, connect_args={"application_name": "host_decision_holder", "options": options}
    )
    waiter = sa.create_engine(
        database_url, connect_args={"application_name": "host_decision_waiter", "options": options}
    )
    observer = sa.create_engine(database_url, connect_args={"options": options}, isolation_level="AUTOCOMMIT")
    tenant = TenantId("ten_host_lock")
    now = datetime.now(UTC)
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="host-lock", created_at=now, created_by="test"):
            pass
        store = HostRegistryStore(scoped_connections(waiter, tenant))
        runners = RunnerRegistryStore(scoped_connections(waiter, tenant))
        store.add(HostAddition("host-one", "one", "hash", now, "test"))
        runners.add(RunnerAddition("runner-one", "runner", "runner-hash", now, "test"))
        namespaces = {
            "sample": ("host-external-usage", json.dumps(["host-one", "slug"])),
            "miss": ("host-external-usage-miss", json.dumps(["host-one", "slug"])),
            "water": ("host-high-water", "host-one"),
        }
        namespace, key = namespaces.get(operation, ("host-live-name-registry", "registry"))
        if omit_lock:
            monkeypatch.setattr(host_module, "lock_keys", lambda *args: None)

        def act() -> None:
            if operation == "add":
                store.add(HostAddition("host-two", "two", "hash2", now, "test"))
            elif operation == "report":
                store.record_report("host-one", HostReport("new", None, (), (), ()), name="new", at=now)
            elif operation == "adopt":
                store.adopt("host-one", ())
            elif operation == "retire":
                store.record_retirement(
                    "host-one", fact=HostLifecycleFact("host-one", True, now, "test"), at=now, by="test"
                )
            elif operation == "enroll":
                store.rotate_token(HostTokenRotation("host-one", "new-hash", now, "test"))
            elif operation == "runner-add":
                runners.add(RunnerAddition("runner-two", "runner", "runner-hash-2", now, "test", host_id="host-one"))
            elif operation == "runner-retire":
                runners.record_lifecycle("runner-one", retired=True, at=now, by="test")
            elif operation == "sample":
                store.record_external_usage(
                    "host-one", slug="slug", name="name", sampled_at=now, windows_json="[]", at=now
                )
            elif operation == "miss":
                store.record_external_usage_miss(
                    "host-one", slug="slug", name="name", missed_at=now, reason="miss", at=now
                )
            else:
                store.set_high_water("host-one", seq=1, at=now)

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_decision_key") as conn:
                lock_keys(conn, namespace, [key])
                future = actors.submit(act)
                if omit_lock:
                    future.result(timeout=5)
                    continue_check = False
                else:
                    continue_check = True
                deadline = monotonic() + 5
                blocked = False
                with observer.connect() as monitor:
                    while continue_check and monotonic() < deadline:
                        blocked = monitor.execute(
                            sa.text("""
                            SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                              JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                              WHERE w.application_name = 'host_decision_waiter'
                                AND h.application_name = 'host_decision_holder')
                        """)
                        ).scalar_one()
                        if blocked:
                            break
                        assert not future.done(), "decision completed without its key lock"
                        Event().wait(0.02)
                    if continue_check and not blocked:
                        pytest.fail("decision did not block on its key")
            future.result(timeout=10)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


def test_competing_add_and_retirement_commit_exactly_once(database_url: str) -> None:
    engine = sa.create_engine(database_url, connect_args={"options": "-c statement_timeout=8000"})
    tenant = TenantId("ten_host_winners")
    now = datetime.now(UTC)
    try:
        scoped = scoped_connections(engine, tenant)
        with scoped.founding("found", name="host-winners", created_at=now, created_by="test"):
            pass
        store = HostRegistryStore(scoped)

        def add(number: int) -> bool:
            try:
                store.add(HostAddition(f"host-{number}", "same", f"hash-{number}", now, "test"))
            except HostNameTaken:
                return False
            return True

        with ThreadPoolExecutor(max_workers=2) as actors:
            assert sorted(actors.map(add, (1, 2))) == [False, True]
        hosts = store.list_hosts()
        assert len(hosts) == 1
        fact = HostLifecycleFact(hosts[0].host_id, True, now, "test")
        with ThreadPoolExecutor(max_workers=2) as actors:
            results = list(
                actors.map(lambda _: store.record_retirement(fact.host_id, fact=fact, at=now, by="test"), range(2))
            )
        assert sum(fact_id is not None for fact_id, _ in results) == 1
        assert sum(revocation_id is not None for _, revocation_id in results) == 1
    finally:
        engine.dispose()
