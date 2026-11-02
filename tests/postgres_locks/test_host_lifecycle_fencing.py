"""PostgreSQL host-row and live-name decision fences, without tenant admission on the blocker."""

from concurrent.futures import ThreadPoolExecutor
from datetime import UTC, datetime
from threading import Event
from time import monotonic

import pytest
import sqlalchemy as sa

import blizzard.hub.store.internal.host_registry_store as host_module
import blizzard.hub.store.internal.runner_registry_store as runner_module
from blizzard.hub.domain.runners.host import HostAddition, HostLifecycleFact, HostNameTaken, HostReport, HostRetired
from blizzard.hub.domain.runners.registration import RunnerAddition, RunnerRetired
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import lock_keys
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.tenancy import TenantId
from tests.support import scoped_connections

pytestmark = pytest.mark.postgres_lock


def _wait_for_block(observer, future):
    deadline = monotonic() + 5
    with observer.connect() as conn:
        while monotonic() < deadline:
            if conn.execute(
                sa.text("""
                SELECT EXISTS (SELECT 1 FROM pg_stat_activity w
                  JOIN pg_stat_activity h ON h.pid = ANY(pg_blocking_pids(w.pid))
                  WHERE w.application_name = 'lifecycle_waiter' AND h.application_name = 'lifecycle_holder')
            """)
            ).scalar_one():
                return
            assert not future.done(), "operation completed without waiting for the blocker"
            Event().wait(0.02)
    pytest.fail("PostgreSQL did not report the expected blocker")


@pytest.mark.parametrize("operation", ["retire", "add", "adopt", "release"])
@pytest.mark.parametrize("same_row", [True, False])
def test_host_row_fences_lifecycle_operations(database_url, operation, same_row):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_lifecycle_fence")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="lifecycle", created_at=now, created_by="test"):
            pass
        hosts = HostRegistryStore(scoped)
        runners = RunnerRegistryStore(scoped)
        for host in ("host", "other"):
            hosts.add(HostAddition(host, host, host + "-hash", now, "test"))
        if operation in ("adopt", "release"):
            runners.add(
                RunnerAddition(
                    "runner", "runner", "runner-hash", now, "test", host_id="host" if operation == "release" else None
                )
            )
        waiting = scoped_connections(waiter, tenant)

        def act():
            if operation == "retire":
                return HostRegistryStore(waiting).record_retirement(
                    "host", fact=HostLifecycleFact("host", True, now, "test"), at=now, by="test"
                )
            if operation == "add":
                return RunnerRegistryStore(waiting).add(
                    RunnerAddition("runner", "runner", "runner-hash", now, "test", host_id="host")
                )
            if operation == "adopt":
                return HostRegistryStore(waiting).adopt("host", ("runner",))
            return HostRegistryStore(waiting).release("host", ("runner",))

        with ThreadPoolExecutor(max_workers=1) as actors:
            # read() deliberately avoids the tenant-admission write lock.
            with scoped.read("hold_host_row") as conn:
                conn.execute(
                    scoped.update(s.hosts)
                    .where(s.hosts.c.host_id == ("host" if same_row else "other"))
                    .values(host_id=s.hosts.c.host_id)
                )
                future = actors.submit(act)
                if same_row:
                    _wait_for_block(observer, future)
                else:
                    future.result(timeout=5)
            future.result(timeout=10)
        if operation == "retire":
            host = hosts.get_host("host")
            assert host is not None and host.retired
        else:
            runner = runners.get_runner("runner")
            assert runner is not None and runner.host_id == (None if operation == "release" else "host")
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["reinstate", "add_adopting"])
@pytest.mark.parametrize("omit_lock", [False, True])
def test_retirement_registry_decision_key(database_url, operation, omit_lock, monkeypatch):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_retirement_decision")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="decision", created_at=now, created_by="test"):
            pass
        hosts = HostRegistryStore(scoped)
        runners = RunnerRegistryStore(scoped)
        hosts.add(HostAddition("host", "host", "hash", now, "test"))
        runners.add(
            RunnerAddition(
                "runner", "runner", "runner-hash", now, "test", host_id="host" if operation == "reinstate" else None
            )
        )
        if operation == "reinstate":
            runners.record_lifecycle("runner", retired=True, at=now, by="test")
        registered = runners.get_runner("runner")
        assert registered is not None
        generation = registered.lifecycle_generation
        waiting = scoped_connections(waiter, tenant)
        if omit_lock:
            if operation == "reinstate":
                monkeypatch.setattr(runner_module, "lock_host_registry", lambda conn: None)
            else:
                monkeypatch.setattr(host_module, "lock_host_registry", lambda conn: None)

        def act():
            if operation == "reinstate":
                return RunnerRegistryStore(waiting).record_lifecycle(
                    "runner", retired=False, at=now, by="test", expected_generation=generation
                )
            return HostRegistryStore(waiting).add(
                HostAddition("new", "new", "new-hash", now, "test", adopt=("runner",))
            )

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("hold_registry_key") as conn:
                lock_keys(conn, "host-live-name-registry", ["registry"])
                future = actors.submit(act)
                if omit_lock:
                    future.result(timeout=5)
                else:
                    _wait_for_block(observer, future)
            future.result(timeout=10)
        runner = runners.get_runner("runner")
        assert runner is not None
        assert not runner.retired
        assert runner.host_id == ("host" if operation == "reinstate" else "new")
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["reinstate", "add_adopting"])
def test_retirement_decision_is_rechecked_after_registry_wait(database_url, operation):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_retirement_recheck")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="recheck", created_at=now, created_by="test"):
            pass
        hosts = HostRegistryStore(scoped)
        runners = RunnerRegistryStore(scoped)
        hosts.add(HostAddition("host", "host", "hash", now, "test"))
        runners.add(
            RunnerAddition(
                "runner", "runner", "runner-hash", now, "test", host_id="host" if operation == "reinstate" else None
            )
        )
        if operation == "reinstate":
            runners.record_lifecycle("runner", retired=True, at=now, by="test")
        registered = runners.get_runner("runner")
        assert registered is not None
        generation = registered.lifecycle_generation
        waiting = scoped_connections(waiter, tenant)

        def act():
            if operation == "reinstate":
                with pytest.raises(HostRetired):
                    RunnerRegistryStore(waiting).record_lifecycle(
                        "runner", retired=False, at=now, by="test", expected_generation=generation
                    )
            else:
                with pytest.raises(RunnerRetired):
                    HostRegistryStore(waiting).add(
                        HostAddition("new", "new", "new-hash", now, "test", adopt=("runner",))
                    )

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("commit_retirement") as conn:
                lock_keys(conn, "host-live-name-registry", ["registry"])
                table = s.host_lifecycle_facts if operation == "reinstate" else s.runner_lifecycle_facts
                identity = {"host_id": "host"} if operation == "reinstate" else {"runner_id": "runner"}
                conn.execute(scoped.insert(table).values(**identity, retired=True, set_at=now, set_by="test"))
                future = actors.submit(act)
                _wait_for_block(observer, future)
                conn.commit()
            future.result(timeout=10)
        runner = runners.get_runner("runner")
        assert runner is not None and runner.retired
        assert runner.host_id == ("host" if operation == "reinstate" else None)
        assert hosts.get_host("new") is None
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("operation", ["add", "adopt", "release"])
def test_retirement_commit_is_rechecked_after_row_wait(database_url, operation):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_retirement_fence")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="retirement", created_at=now, created_by="test"):
            pass
        HostRegistryStore(scoped).add(HostAddition("host", "host", "hash", now, "test"))
        runners = RunnerRegistryStore(scoped)
        if operation != "add":
            runners.add(
                RunnerAddition(
                    "runner", "runner", "runner-hash", now, "test", host_id="host" if operation == "release" else None
                )
            )
        waiting = scoped_connections(waiter, tenant)

        def act():
            with pytest.raises(HostRetired):
                if operation == "add":
                    RunnerRegistryStore(waiting).add(
                        RunnerAddition("runner", "runner", "runner-hash", now, "test", host_id="host")
                    )
                elif operation == "adopt":
                    HostRegistryStore(waiting).adopt("host", ("runner",))
                else:
                    HostRegistryStore(waiting).release("host", ("runner",))

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("retire_without_admission") as conn:
                conn.execute(
                    scoped.update(s.hosts).where(s.hosts.c.host_id == "host").values(host_id=s.hosts.c.host_id)
                )
                conn.execute(
                    scoped.insert(s.host_lifecycle_facts).values(
                        host_id="host", retired=True, set_at=now, set_by="test"
                    )
                )
                future = actors.submit(act)
                _wait_for_block(observer, future)
                conn.commit()
            future.result(timeout=10)
        runner = runners.get_runner("runner")
        if operation == "add":
            assert runner is None
        else:
            assert runner is not None and runner.host_id == ("host" if operation == "release" else None)
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()


@pytest.mark.parametrize("winner", ["add", "report"])
def test_add_and_report_cannot_claim_same_live_name(database_url, winner):
    options = "-c statement_timeout=8000"
    holder = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_holder", "options": options})
    waiter = sa.create_engine(database_url, connect_args={"application_name": "lifecycle_waiter", "options": options})
    observer = sa.create_engine(database_url, connect_args={"options": options})
    now = datetime.now(UTC)
    tenant = TenantId("ten_name_fence")
    try:
        scoped = scoped_connections(holder, tenant)
        with scoped.founding("found", name="names", created_at=now, created_by="test"):
            pass
        HostRegistryStore(scoped).add(HostAddition("existing", "old", "hash", now, "test"))
        waiting = HostRegistryStore(scoped_connections(waiter, tenant))

        def act():
            with pytest.raises(HostNameTaken):
                if winner == "add":
                    waiting.record_report("existing", HostReport("same", None, (), (), ()), name="same", at=now)
                else:
                    waiting.add(HostAddition("new", "same", "new-hash", now, "test"))

        with ThreadPoolExecutor(max_workers=1) as actors:
            with scoped.read("claim_name") as conn:
                from blizzard.hub.store.internal.chunk_rows import lock_keys

                lock_keys(conn, "host-name", ["registry"])
                if winner == "add":
                    conn.execute(
                        scoped.insert(s.hosts).values(
                            host_id="new", name="same", token_hash="new-hash", added_at=now, added_by="test"
                        )
                    )
                else:
                    conn.execute(scoped.update(s.hosts).where(s.hosts.c.host_id == "existing").values(name="same"))
                future = actors.submit(act)
                _wait_for_block(observer, future)
                conn.commit()
            future.result(timeout=10)
        assert [h.name for h in HostRegistryStore(scoped).list_hosts()].count("same") == 1
    finally:
        holder.dispose()
        waiter.dispose()
        observer.dispose()
