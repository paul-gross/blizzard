"""Exercise hub transaction lock ordering on a disposable Postgres database."""

from __future__ import annotations

import os
import signal
import sys
import tempfile
import threading
import time
from collections.abc import Callable, Sequence
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import sqlalchemy as sa
from sqlalchemy import Connection, Engine
from structlog import get_logger

from blizzard.foundation.clock import FixedClock
from blizzard.hub.config import HubConfig
from blizzard.hub.domain.cadence.errands import ErrandKey
from blizzard.hub.domain.cadence.mint import mint_lock_keys
from blizzard.hub.domain.chunk.ports.queue import BACKLOG_RANKING_LOCK, READY_RANKING_LOCK
from blizzard.hub.domain.config.forges import ForgeId, forge_lock
from blizzard.hub.domain.config.secrets import secret_lock
from blizzard.hub.domain.kernel.locks import LockKey, LockNamespace
from blizzard.hub.domain.tenancy.teardown import CloseOutcome, LastTenant, TenantTeardown
from blizzard.hub.domain.urgency.model import UrgencyId
from blizzard.hub.domain.urgency.ports import tenant_settings_lock, urgency_lock, urgency_order_lock
from blizzard.hub.domain.urgency.services import default_lock_keys
from blizzard.hub.runtime import Migrations
from blizzard.hub.store import schema as s
from blizzard.hub.store import transactions as tx
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.tenant_teardown_store import DRAIN_ORDER, TenantTeardownStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantClosed, TenantId, UnknownTenant

_TENANT = "uow_proof"
_TIMEOUT = 10.0
_CLOCK = FixedClock(datetime(2026, 7, 12, tzinfo=UTC))
_PROBE_NAMESPACE = "proof"


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


_LockSet = tuple[list[str], list[LockKey]]

_RETIRING = UrgencyId("urg_proof")
#: The cadence mint's lock set against the queue's own promote — a fresh chunk naming no row yet, the errand, both
#: ranking locks, and the default ``urg_proof``'s, beside the promoted chunk and both ranking locks — run each way
#: round. Each first writer holds every lock it declares, so the second blocks on the ranking locks and lands only
#: once the first commits.
_MINT: _LockSet = (
    ["n"],
    mint_lock_keys(ErrandKey("chr_proof", "scp_proof"), promote=True, urgencies=default_lock_keys(_RETIRING)),
)
_PROMOTE: _LockSet = (["a"], [READY_RANKING_LOCK, BACKLOG_RANKING_LOCK])
#: A retire of ``urg_proof`` against each default-taking mint and a settings edit, both orders, on the settings' lock.
_RETIRE: _LockSet = (
    ["a"],
    [tenant_settings_lock(), urgency_order_lock(), urgency_lock(_RETIRING), urgency_lock(UrgencyId("urg_other"))],
)
#: A chunk edit setting ``a`` to ``urg_proof`` against the retire, both orders; both lock the row before the key.
_CHUNK_EDIT: _LockSet = (["a"], [urgency_lock(_RETIRING)])
_DEFAULT_MINT: _LockSet = (["n"], default_lock_keys(_RETIRING))
_SETTINGS_EDIT: _LockSet = ([], [tenant_settings_lock(), urgency_lock(_RETIRING)])
#: A config apply creating urgencies, setting the order, and making ``urg_proof`` the default — the settings', the
#: set's, and the named urgency's locks — against the retire, each way round: the settings' lock serializes them, so
#: a retire never commits between the apply's re-checks and its writes.
_APPLY: _LockSet = ([], [tenant_settings_lock(), urgency_order_lock(), urgency_lock(_RETIRING)])
#: A reorder or a create — the set's lock alone — against the retire and against an enable of ``urg_proof``, each way
#: round: both take the set's lock, so the urgencies a reorder decides are live hold still until it commits.
_REORDER: _LockSet = ([], [urgency_order_lock()])
_ENABLE: _LockSet = ([], [urgency_order_lock(), urgency_lock(_RETIRING)])
#: A stop or an operator complete of ``a`` — the chunk row alone — against the retire, each way round: every write
#: that finishes a chunk takes its row lock, so no chunk the retire moves finishes between its re-read and its move.
_FINISH: _LockSet = (["a"], [])
#: A forge retire — the forge's key — against an enable of a repository naming it — the forge's and its secret's keys —
#: and a secret retire against an enable of a work source naming it, each way round: the shared key serializes them, so
#: a retire never commits between a referrer's enable judging the forge or secret live and its write.
_FORGE = forge_lock(ForgeId("frg_proof"))
_SECRET = secret_lock("tok_proof")
_FORGE_RETIRE: _LockSet = ([], [_FORGE])
_SECRET_RETIRE: _LockSet = ([], [_SECRET])
_REFERRER_ENABLE: _LockSet = ([], [_FORGE, _SECRET])
_LOCK_SETS: dict[str, tuple[_LockSet, _LockSet]] = {
    "cadence mint, then promote": (_MINT, _PROMOTE),
    "promote, then cadence mint": (_PROMOTE, _MINT),
    "urgency retire, then mint": (_RETIRE, _DEFAULT_MINT),
    "mint, then urgency retire": (_DEFAULT_MINT, _RETIRE),
    "urgency retire, then cadence mint": (_RETIRE, _MINT),
    "cadence mint, then urgency retire": (_MINT, _RETIRE),
    "urgency retire, then settings edit": (_RETIRE, _SETTINGS_EDIT),
    "settings edit, then urgency retire": (_SETTINGS_EDIT, _RETIRE),
    "urgency retire, then chunk edit": (_RETIRE, _CHUNK_EDIT),
    "chunk edit, then urgency retire": (_CHUNK_EDIT, _RETIRE),
    "config apply, then urgency retire": (_APPLY, _RETIRE),
    "urgency retire, then config apply": (_RETIRE, _APPLY),
    "urgency retire, then reorder": (_RETIRE, _REORDER),
    "reorder, then urgency retire": (_REORDER, _RETIRE),
    "urgency retire, then stop": (_RETIRE, _FINISH),
    "stop, then urgency retire": (_FINISH, _RETIRE),
    "urgency retire, then complete": (_RETIRE, _FINISH),
    "complete, then urgency retire": (_FINISH, _RETIRE),
    "urgency enable, then reorder": (_ENABLE, _REORDER),
    "reorder, then urgency enable": (_REORDER, _ENABLE),
    "forge retire, then repository enable": (_FORGE_RETIRE, _REFERRER_ENABLE),
    "repository enable, then forge retire": (_REFERRER_ENABLE, _FORGE_RETIRE),
    "secret retire, then work-source enable": (_SECRET_RETIRE, _REFERRER_ENABLE),
    "work-source enable, then secret retire": (_REFERRER_ENABLE, _SECRET_RETIRE),
}


def _canonical(chunks: Sequence[str], keys: Sequence[LockKey]) -> list[str]:
    """The order the store takes a lock set in: chunk ids, then keys by namespace and key."""
    return [*sorted(chunks), *(f"{lock.namespace}/{lock.key}" for lock in sorted(keys))]


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

    def key_lock(conn: Connection, namespace: LockNamespace, keys: Sequence[str]) -> None:
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
                    keys = [LockKey(LockNamespace.WORK_REF, "b"), LockKey(LockNamespace.CADENCE_ERRAND, "a")]
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
            expected = ["a", "b"] if kind == "chunks" else ["cadence_errand/a", "work_ref/b"]
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


class _Events:
    """A stand-in for the hub event log: the proof reads no events."""

    def record(self, **_: object) -> None:
        return None


def _unit_pid(transaction: object) -> int:
    """The backend pid of the connection a unit's transaction holds."""
    return _pid(cast("Any", transaction).unit.conn)


def _wait_for_blocked_by(engine: Engine, holder: int) -> None:
    """Wait until some backend is blocked by backend ``holder``."""
    deadline = time.monotonic() + _TIMEOUT
    with engine.connect() as conn:
        while time.monotonic() < deadline:
            blocked = conn.execute(
                sa.text("SELECT pid FROM pg_stat_activity WHERE :holder = ANY(pg_blocking_pids(pid))"),
                {"holder": holder},
            ).all()
            if blocked:
                return
            time.sleep(0.02)
    raise RuntimeError(f"no backend blocked on backend {holder}")


class _Hub:
    """The production pieces over one migrated engine, as ``composition`` wires them."""

    def __init__(self, engine: Engine) -> None:
        self.engine = engine
        self.errors = HubStoreErrorFactory(get_logger("uow_postgres"))
        self.connections = HubStoreConnections(engine, self.errors)
        self.store = TenantTeardownStore(self.connections)
        self.teardown = TenantTeardown(
            teardowns=self.store,
            units=self.unit,
            closed=lambda _tenant: None,
            events=_Events(),
            clock=_CLOCK,
        )

    def scoped(self, tenant: str) -> ScopedConnections:
        return self.connections.scoped(StoreScope(TenantId(tenant)))

    def unit(self, tenant: TenantId) -> tx.HubTransactions:
        return tx.HubTransactions(self.scoped(tenant))

    def create_tenant(self, tenant: str) -> None:
        with self.engine.begin() as conn:
            conn.execute(
                sa.insert(s.tenants).values(tenant_id=tenant, name=tenant, created_at=_CLOCK.now(), created_by="proof")
            )

    def probe_rows(self, tenant: str) -> int:
        with self.engine.connect() as conn:
            return int(
                conn.execute(
                    sa.select(sa.func.count())
                    .select_from(s.keyed_locks)
                    .where(s.keyed_locks.c.tenant_id == tenant, s.keyed_locks.c.namespace == _PROBE_NAMESPACE)
                ).scalar_one()
            )


class _Threads:
    """Named worker threads whose outcomes (the exception each ended on, or ``None``) are kept by name."""

    def __init__(self) -> None:
        self.outcomes: dict[str, BaseException | None] = {}
        self.threads: list[threading.Thread] = []

    def start(self, name: str, target: Callable[[], object]) -> None:
        def run() -> None:
            try:
                target()
                self.outcomes[name] = None
            except BaseException as exc:
                self.outcomes[name] = exc

        thread = threading.Thread(target=run, name=name, daemon=True)
        self.threads.append(thread)
        thread.start()

    def join(self) -> None:
        for thread in self.threads:
            thread.join(_TIMEOUT)
            if thread.is_alive():
                raise RuntimeError(f"thread {thread.name} did not finish")


def _writes_a_probe(hub: _Hub, tenant: str, key: str, hold: Callable[[int], None] | None = None) -> None:
    """A scoped write that locks one key (inserting its ``keyed_locks`` row) and then holds, if asked."""
    with hub.unit(TenantId(tenant)).begin() as unit:
        unit.lock_set(keys=[(_PROBE_NAMESPACE, key)])
        if hold is not None:
            hold(_unit_pid(unit))


def _close_holding(hub: _Hub, tenant: str, held: threading.Event, release: threading.Event, pids: dict[str, int]):
    """Close ``tenant`` through the production teardown, holding after the deletion fact is written, before commit."""
    original = hub.store.close

    def close(transaction: Any, tenant: TenantId, *, by: str, at: datetime) -> None:
        original(transaction, tenant, by=by, at=at)
        pids["closer"] = _unit_pid(transaction)
        held.set()
        if not release.wait(_TIMEOUT):
            raise TimeoutError("close was not released")

    vars(hub.store)["close"] = close
    return lambda: hub.teardown.close(TenantId(tenant), by="proof")


def _case_closed_tenant_write_holds(hub: _Hub) -> None:
    """A write holds its tenant's row while a close of the tenant waits: the write commits, then the close closes."""
    tenant = "closing_after_write"
    hub.create_tenant(tenant)
    held, release = threading.Event(), threading.Event()
    pids: dict[str, int] = {}
    threads = _Threads()

    def hold(pid: int) -> None:
        pids["writer"] = pid
        held.set()
        if not release.wait(_TIMEOUT):
            raise TimeoutError("write was not released")

    threads.start("write", lambda: _writes_a_probe(hub, tenant, "w", hold))
    try:
        if not held.wait(_TIMEOUT):
            raise RuntimeError(f"write failed to hold: {threads.outcomes}")
        outcome: list[CloseOutcome] = []
        threads.start("close", lambda: outcome.append(hub.teardown.close(TenantId(tenant), by="proof")))
        _wait_for_blocked_by(hub.engine, pids["writer"])
    finally:
        release.set()
        threads.join()
    failures = {name: exc for name, exc in threads.outcomes.items() if exc is not None}
    if failures:
        raise RuntimeError(f"expected both to complete: {failures}")
    if hub.probe_rows(tenant) != 1 or outcome != [CloseOutcome.CLOSED]:
        raise RuntimeError(f"the write did not commit before the close: rows={hub.probe_rows(tenant)} close={outcome}")


def _case_closed_tenant_close_holds(hub: _Hub) -> None:
    """A close holds the tenant's row while a write waits: the write refuses ``TenantClosed`` and writes nothing."""
    tenant = "closing_before_write"
    hub.create_tenant(tenant)
    held, release = threading.Event(), threading.Event()
    pids: dict[str, int] = {}
    threads = _Threads()
    threads.start("close", _close_holding(hub, tenant, held, release, pids))
    try:
        if not held.wait(_TIMEOUT):
            raise RuntimeError(f"close failed to hold: {threads.outcomes}")
        threads.start("write", lambda: _writes_a_probe(hub, tenant, "w"))
        _wait_for_blocked_by(hub.engine, pids["closer"])
    finally:
        release.set()
        threads.join()
        vars(hub.store).pop("close", None)
    if threads.outcomes.get("close") is not None:
        raise RuntimeError(f"the close failed: {threads.outcomes['close']!r}")
    refusal = threads.outcomes.get("write")
    if not isinstance(refusal, TenantClosed):
        raise RuntimeError(f"a write that waited on a committed close was not refused: {refusal!r}")
    if hub.probe_rows(tenant):
        raise RuntimeError("a refused write left a row in the closed tenant")


def _case_two_closes_last_tenants(hub: _Hub) -> None:
    """Two closes of the last two open tenants: one commits, the other refuses ``LastTenant``."""
    first_tenant, second_tenant = "last_one", "last_two"
    hub.create_tenant(first_tenant)
    hub.create_tenant(second_tenant)
    with hub.engine.connect() as conn:
        open_now = (
            conn.execute(
                sa.select(s.tenants.c.tenant_id).where(
                    ~sa.exists().where(s.tenant_deletions.c.tenant_id == s.tenants.c.tenant_id)
                )
            )
            .scalars()
            .all()
        )
    for other in open_now:
        if other not in (first_tenant, second_tenant):
            hub.teardown.close(TenantId(other), by="proof")
    held, release = threading.Event(), threading.Event()
    pids: dict[str, int] = {}
    threads = _Threads()
    threads.start("first", _close_holding(hub, first_tenant, held, release, pids))
    try:
        if not held.wait(_TIMEOUT):
            raise RuntimeError(f"first close failed to hold: {threads.outcomes}")
        vars(hub.store).pop("close", None)
        threads.start("second", lambda: hub.teardown.close(TenantId(second_tenant), by="proof"))
        _wait_for_blocked_by(hub.engine, pids["closer"])
    finally:
        release.set()
        threads.join()
        vars(hub.store).pop("close", None)
    if threads.outcomes.get("first") is not None:
        raise RuntimeError(f"the first close failed: {threads.outcomes['first']!r}")
    if not isinstance(threads.outcomes.get("second"), LastTenant):
        raise RuntimeError(f"the second close was not refused LastTenant: {threads.outcomes.get('second')!r}")
    if hub.store.deletion(TenantId(second_tenant)) is not None:
        raise RuntimeError("both of the last two tenants closed")


def _case_drain_under_foreign_keys(hub: _Hub) -> None:
    """A tenant with a row in every table it owns drains and completes with foreign keys enforced."""
    from hub_tenant_seed import seed_tenant

    tenant = "draining"
    hub.create_tenant("draining_spare")
    hub.create_tenant(tenant)
    connections = hub.scoped(tenant)
    with connections.write("seed_tenant") as conn:
        seed_tenant(connections, conn)
    with hub.engine.connect() as conn:
        empty = [
            table.name
            for table in DRAIN_ORDER
            if conn.execute(
                sa.select(sa.literal(1)).select_from(table).where(table.c.tenant_id == tenant).limit(1)
            ).first()
            is None
        ]
    if empty:
        raise RuntimeError(f"the seed left tables of DRAIN_ORDER with no row of the tenant: {empty}")
    hub.teardown.close(TenantId(tenant), by="proof")
    hub.teardown.advance(TenantId(tenant), budget=1_000_000)
    with hub.engine.connect() as conn:
        left = [
            table.name
            for table in DRAIN_ORDER
            if conn.execute(
                sa.select(sa.literal(1)).select_from(table).where(table.c.tenant_id == tenant).limit(1)
            ).first()
            is not None
        ]
        present = conn.execute(sa.select(s.tenants.c.tenant_id).where(s.tenants.c.tenant_id == tenant)).first()
    deletion = hub.store.deletion(TenantId(tenant))
    if left or present is not None or deletion is None or deletion.deleted_at is None:
        raise RuntimeError(f"the drain left rows in {left}, tenant row {present}, deletion {deletion}")


def _case_different_keys_do_not_exclude(hub: _Hub) -> None:
    """Two units of one tenant declaring different keys, with a guard that takes no tenant-row lock: neither waits."""
    tenant = "keys_apart"
    hub.create_tenant(tenant)
    scope = StoreScope(TenantId(tenant))
    store = _CompatibleGuard(hub.engine, hub.errors, scope)
    held, release, second_done = threading.Event(), threading.Event(), threading.Event()
    threads = _Threads()

    def write(key: str, hold: bool) -> None:
        with tx.HubTransactions(store).begin() as unit:
            unit.lock_set(keys=[(_PROBE_NAMESPACE, key)])
            if hold:
                held.set()
                if not release.wait(_TIMEOUT):
                    raise TimeoutError("first writer was not released")
            else:
                second_done.set()

    threads.start("first", lambda: write("k1", True))
    try:
        if not held.wait(_TIMEOUT):
            raise RuntimeError(f"first writer failed to hold: {threads.outcomes}")
        threads.start("second", lambda: write("k2", False))
        if not second_done.wait(_TIMEOUT):
            raise RuntimeError("a unit declaring a different key waited on the first")
    finally:
        release.set()
        threads.join()
    failures = {name: exc for name, exc in threads.outcomes.items() if exc is not None}
    if failures or hub.probe_rows(tenant) != 2:
        raise RuntimeError(f"expected both units to commit their keys: {failures}, rows={hub.probe_rows(tenant)}")


_MIGRATED_CASES: dict[str, Callable[[_Hub], None]] = {
    "closed tenant, write holds then close": _case_closed_tenant_write_holds,
    "closed tenant, close holds then write": _case_closed_tenant_close_holds,
    "two closes of the last two open tenants": _case_two_closes_last_tenants,
    "drain under foreign keys": _case_drain_under_foreign_keys,
    "different keyed locks do not exclude": _case_different_keys_do_not_exclude,
}


def _run_migrated_cases(url: str) -> list[str]:
    """Run the cases that need the migrated hub schema; the names of those that failed."""
    from tests.rehearsal.stores import postgres_database

    failed: list[str] = []
    with tempfile.TemporaryDirectory() as root, postgres_database(url) as database:
        Migrations(HubConfig(root=Path(root), db_url=database), _CLOCK).runner.upgrade("head")
        engine = sa.create_engine(database, connect_args={"options": "-c lock_timeout=7000 -c statement_timeout=9000"})
        try:
            hub = _Hub(engine)
            for name, case in _MIGRATED_CASES.items():
                try:
                    case(hub)
                except Exception as exc:
                    print(f"FAIL {name}: {exc!r}")
                    failed.append(name)
                else:
                    print(f"PASS {name}: migrated schema")
        finally:
            engine.dispose()
    return failed


def main() -> None:
    sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

    url = os.environ.get("BLIZZARD_UOW_POSTGRES_URL")
    if not url:
        raise RuntimeError("BLIZZARD_UOW_POSTGRES_URL must name a disposable-capable Postgres server")
    from tests.rehearsal.stores import postgres_database

    signal.alarm(300)
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
                    sa.text(
                        "INSERT INTO keyed_locks VALUES (:tenant, 'cadence_errand', 'a'), (:tenant, 'work_ref', 'b')"
                    ),
                    {"tenant": _TENANT},
                )
            _run_pair(engine, kind="chunks", production_guard=False)
            _run_pair(engine, kind="keys", production_guard=False)
            _run_pair(engine, kind="chunks", production_guard=True)
            for kind in _LOCK_SETS:
                _run_pair(engine, kind=kind, production_guard=False)
        finally:
            engine.dispose()
    failed = _run_migrated_cases(url)
    if failed:
        raise SystemExit(f"FAILED: {', '.join(failed)}")


if __name__ == "__main__":
    main()
