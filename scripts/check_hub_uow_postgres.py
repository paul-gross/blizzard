"""Exercise hub transaction lock ordering on a disposable Postgres database."""

from __future__ import annotations

import os
import signal
import sys
import threading
import time
from collections.abc import Callable, Sequence
from pathlib import Path

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from structlog import get_logger

from blizzard.hub.domain.cadence.errands import ErrandKey
from blizzard.hub.domain.cadence.mint import mint_lock_keys
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK, READY_RANKING_LOCK
from blizzard.hub.store import schema as s
from blizzard.hub.store import transactions as tx
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant

_TENANT = "uow_proof"
_TIMEOUT = 10.0


class _CompatibleGuard(ScopedConnections):
    """Test-only open check that does not serialize writers on the tenant row.

    This intentionally does not provide teardown exclusion and must never be used in production.
    """

    def _lock_open_tenant(self, conn: Connection) -> None:
        present = conn.execute(
            sa.select(s.tenants.c.tenant_id).where(s.tenants.c.tenant_id == self.scope.tenant)
        ).first()
        if present is None:
            raise UnknownTenant(self.scope.tenant)
        if conn.execute(
            sa.select(s.tenant_deletions.c.tenant_id).where(s.tenant_deletions.c.tenant_id == self.scope.tenant)
        ).first():
            raise TenantClosed(self.scope.tenant)


_LockSet = tuple[list[str], list[tuple[str, str]]]

#: The cadence mint's lock set against the queue's own promote — a fresh chunk naming no row yet, the errand, and
#: both ranking locks, beside the promoted chunk and both ranking locks — run each way round. Each first writer holds
#: every lock it declares, so the second blocks on the ranking locks and lands only once the first commits.
_MINT: _LockSet = (["n"], mint_lock_keys(ErrandKey("chr_proof", "scp_proof"), promote=True))
_PROMOTE: _LockSet = (["a"], [READY_RANKING_LOCK, BACKLOG_RANKING_LOCK])
_LOCK_SETS: dict[str, tuple[_LockSet, _LockSet]] = {
    "cadence mint, then promote": (_MINT, _PROMOTE),
    "promote, then cadence mint": (_PROMOTE, _MINT),
}


def _canonical(chunks: Sequence[str], keys: Sequence[tuple[str, str]]) -> list[str]:
    """The order the store takes a lock set in: chunk ids, then keys by namespace and key."""
    return [*sorted(chunks), *(f"{namespace}/{key}" for namespace, key in sorted(keys))]


def _pid(conn: Connection) -> int:
    return int(conn.execute(sa.text("SELECT pg_backend_pid()")).scalar_one())


def _wait_for_block(engine: Engine, waiting: int, holder: int) -> None:
    deadline = time.monotonic() + _TIMEOUT
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            blockers = conn.execute(sa.text("SELECT pg_blocking_pids(:pid)"), {"pid": waiting}).scalar_one()
            if holder in blockers:
                return
            time.sleep(0.02)
    raise RuntimeError(f"backend {waiting} did not block on backend {holder}")


def _run_pair(engine: Engine, *, kind: str, production_guard: bool) -> None:
    errors = HubStoreErrorFactory(get_logger("uow_postgres"))
    scope = StoreScope(TenantId(_TENANT))
    store = (
        HubStoreConnections(engine, errors).scoped(scope)
        if production_guard
        else _CompatibleGuard(engine, errors, scope)
    )
    held = threading.Event()
    second_started = threading.Event()
    release = threading.Event()
    pids: dict[str, int] = {}
    acquired: dict[str, list[str]] = {"first": [], "second": []}
    failures: list[BaseException] = []
    original_chunk, original_keys = tx.lock_chunk_row, tx.lock_keys

    sets = _LOCK_SETS.get(kind)
    # The first writer holds once it has taken every lock it declares, or its first when the two sets mirror.
    hold_after = len(sets[0][0]) + len(sets[0][1]) if sets is not None else 1

    def observe(conn: Connection, name: str, operation: Callable[[], None]) -> None:
        worker = threading.current_thread().name
        pids[worker] = _pid(conn)
        if worker == "second":
            second_started.set()
        operation()
        acquired[worker].append(name)
        if worker == "first" and len(acquired[worker]) == hold_after:
            held.set()
            if not release.wait(_TIMEOUT):
                raise TimeoutError("first writer was not released")

    def chunk_lock(conn: Connection, chunk_id: str) -> None:
        observe(conn, chunk_id, lambda: original_chunk(conn, chunk_id))

    def key_lock(conn: Connection, namespace: str, keys: Sequence[str]) -> None:
        observe(conn, f"{namespace}/{keys[0]}", lambda: original_keys(conn, namespace, keys))

    def writer(reverse: bool) -> None:
        try:
            with tx.HubTransactions(store).begin() as unit:
                if sets is not None:
                    chunks, keys = sets[1] if reverse else sets[0]
                    unit.lock_set(chunks=chunks, keys=keys)
                elif kind == "chunks":
                    unit.lock_chunks(["b", "a"] if reverse else ["a", "b"])
                else:
                    keys = [("z", "b"), ("a", "a")]
                    unit.lock_set(keys=keys if reverse else list(reversed(keys)))
        except BaseException as exc:
            failures.append(exc)

    tx.lock_chunk_row, tx.lock_keys = chunk_lock, key_lock
    first = threading.Thread(target=writer, args=(False,), name="first", daemon=True)
    second = threading.Thread(target=writer, args=(True,), name="second", daemon=True)
    try:
        first.start()
        if not held.wait(_TIMEOUT):
            raise RuntimeError(f"first writer failed to acquire a lock: {failures}")
        second.start()
        if production_guard:
            # The second writer cannot reach the decision lock until the tenant guard returns.
            deadline = time.monotonic() + _TIMEOUT
            with engine.connect() as conn:
                while time.monotonic() < deadline:
                    rows = (
                        conn.execute(
                            sa.text("SELECT pid FROM pg_stat_activity WHERE :holder = ANY(pg_blocking_pids(pid))"),
                            {"holder": pids["first"]},
                        )
                        .scalars()
                        .all()
                    )
                    if rows:
                        break
                    time.sleep(0.02)
                else:
                    raise RuntimeError("production tenant guard did not block the second writer")
            if second_started.is_set():
                raise RuntimeError("second writer reached a decision lock before the production guard released")
        else:
            if not second_started.wait(_TIMEOUT):
                raise RuntimeError(f"second writer failed to reach decision lock: {failures}")
            _wait_for_block(engine, pids["second"], pids["first"])
        release.set()
        first.join(_TIMEOUT)
        second.join(_TIMEOUT)
        if first.is_alive() or second.is_alive() or failures:
            raise RuntimeError(f"writers did not both complete: {failures}")
        if sets is not None:
            expected_by = {worker: _canonical(*sets[index]) for index, worker in enumerate(("first", "second"))}
        else:
            expected = ["a", "b"] if kind == "chunks" else ["a/a", "z/b"]
            expected_by = {"first": expected, "second": expected}
        if acquired != expected_by:
            raise RuntimeError(f"noncanonical lock order: {acquired}")
        print(f"PASS {kind}: {'production guard' if production_guard else 'decision-row contention'}")
    finally:
        release.set()
        first.join(_TIMEOUT)
        if second.ident is not None:
            second.join(_TIMEOUT)
        tx.lock_chunk_row, tx.lock_keys = original_chunk, original_keys


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
    from tests.rehearsal.stores import postgres_database

    url = os.environ.get("BLIZZARD_UOW_POSTGRES_URL")
    if not url:
        raise RuntimeError("BLIZZARD_UOW_POSTGRES_URL must name a disposable-capable Postgres server")
    signal.alarm(90)
    with postgres_database(url) as database:
        engine = sa.create_engine(database, connect_args={"options": "-c lock_timeout=7000 -c statement_timeout=9000"})
        try:
            with engine.begin() as conn:
                # Minimal scratch tables retain the exact columns used by the guards and locks.
                conn.execute(sa.text("CREATE TABLE tenants (tenant_id text PRIMARY KEY, name text NOT NULL)"))
                conn.execute(sa.text("CREATE TABLE tenant_deletions (tenant_id text PRIMARY KEY)"))
                conn.execute(
                    sa.text(
                        "CREATE TABLE chunks (tenant_id text NOT NULL, chunk_id text NOT NULL, "
                        "PRIMARY KEY (tenant_id, chunk_id))"
                    )
                )
                conn.execute(
                    sa.text(
                        "CREATE TABLE keyed_locks (tenant_id text NOT NULL, namespace text NOT NULL, "
                        "key text NOT NULL, PRIMARY KEY (tenant_id, namespace, key))"
                    )
                )
                conn.execute(sa.text("INSERT INTO tenants VALUES (:tenant, 'proof')"), {"tenant": _TENANT})
                conn.execute(sa.text("INSERT INTO chunks VALUES (:tenant, 'a'), (:tenant, 'b')"), {"tenant": _TENANT})
                conn.execute(
                    sa.text("INSERT INTO keyed_locks VALUES (:tenant, 'a', 'a'), (:tenant, 'z', 'b')"),
                    {"tenant": _TENANT},
                )
            _run_pair(engine, kind="chunks", production_guard=False)
            _run_pair(engine, kind="keys", production_guard=False)
            _run_pair(engine, kind="chunks", production_guard=True)
            for kind in _LOCK_SETS:
                _run_pair(engine, kind=kind, production_guard=False)
        finally:
            engine.dispose()


if __name__ == "__main__":
    main()
