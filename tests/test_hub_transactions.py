"""The opaque hub unit's lifetime, lock declaration and statement accounting."""

from collections.abc import Iterator
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from structlog import get_logger

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.domain.chunk.model import Chunk
from blizzard.hub.domain.kernel.transactions import GraduationError, TransactionMisuse
from blizzard.hub.store import schema as s
from blizzard.hub.store import transactions as tx
from blizzard.hub.store.borrowed import BorrowedConnections, PinnedConnections
from blizzard.hub.store.errors import HubStoreConnections, HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_store_factory import build_chunk_stores
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import hub_store_connections, migrate_to, seed_chunk_record, seed_graph

pytestmark = pytest.mark.component


@pytest.fixture
def store(tmp_path: Path) -> Iterator[ScopedConnections]:
    engine = create_engine_from_url(f"sqlite:///{tmp_path / 'uow.db'}")
    s.tenants.create(engine)
    s.tenant_deletions.create(engine)
    s.keyed_locks.create(engine)
    with engine.begin() as conn:
        conn.execute(sa.insert(s.tenants).values(tenant_id="t", name="t", created_at=datetime.now(UTC), created_by="u"))
    yield HubStoreConnections(engine, HubStoreErrorFactory(get_logger("test"))).scoped(StoreScope(TenantId("t")))
    engine.dispose()


def test_commit_and_rollback(store: ScopedConnections) -> None:
    service = tx.HubTransactions(store)
    with service.begin() as unit:
        conn = tx.connection_for(store, unit)
        conn.execute(store.insert(s.keyed_locks).values(namespace="n", key="committed"))
    with pytest.raises(ValueError), service.begin() as unit:
        conn = tx.connection_for(store, unit)
        conn.execute(store.insert(s.keyed_locks).values(namespace="n", key="rolled-back"))
        raise ValueError("abort")
    with store.read("test") as conn:
        assert conn.execute(store.select(s.keyed_locks, s.keyed_locks.c.key)).scalars().all() == ["committed"]


def test_graduation_counts_borrowed_statements_and_rejects_reuse(store: ScopedConnections) -> None:
    service = tx.HubTransactions(store)
    with service.begin() as unit:
        conn = tx.connection_for(store, unit)
        borrowed = PinnedConnections(conn, store._errors).scoped(store.scope)
        snapshot = BorrowedConnections(conn, store._errors).scoped(store.scope)
        with pytest.raises(TransactionMisuse):
            tx.connection_for(snapshot, unit)
        with pytest.raises(TransactionMisuse):
            tx.connection_for(borrowed, unit)
        with borrowed.read("borrowed") as read:
            read.execute(sa.select(1))
        with pytest.raises(GraduationError):
            unit.lock_chunks(["a"])
    with service.begin() as unit:
        locked = unit.lock_keys("n", ["a"])
        with pytest.raises(GraduationError):
            locked.lock_keys("n", ["b"])
        with pytest.raises(TransactionMisuse):
            tx.connection_for(store, unit)
        assert tx.connection_for(store, locked, key=("n", "a")) is not None
        with pytest.raises(TransactionMisuse):
            tx.connection_for(store, locked, key=("n", "b"))
    with pytest.raises(TransactionMisuse):
        tx.connection_for(store, locked)


def test_lock_set_is_sorted_and_scope_is_checked(store: ScopedConnections, monkeypatch: pytest.MonkeyPatch) -> None:
    calls: list[tuple[str, ...]] = []
    monkeypatch.setattr(tx, "lock_chunk_row", lambda conn, chunk: calls.append(("chunk", chunk)))
    monkeypatch.setattr(tx, "lock_keys", lambda conn, namespace, keys: calls.append((namespace, *keys)))
    same = HubStoreConnections(store._engine, store._errors).scoped(store.scope)
    other = HubStoreConnections(store._engine, store._errors).scoped(StoreScope(TenantId("other")))
    with tx.HubTransactions(store).begin() as unit:
        locked = unit.lock_set(chunks=["b", "a", "b"], keys=[("z", "a"), ("a", "z"), ("a", "a")])
        assert calls == [("chunk", "a"), ("chunk", "b"), ("a", "a"), ("a", "z"), ("z", "a")]
        assert tx.connection_for(store, locked, chunk="a") is not None
        assert tx.connection_for(same, locked, chunk="a") is not None
        with pytest.raises(TransactionMisuse):
            tx.connection_for(store, locked, chunk="missing")
        with pytest.raises(TransactionMisuse):
            tx.connection_for(other, locked)


def test_real_repositories_share_commit_and_rollback(tmp_path: Path) -> None:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    _, engine = migrate_to(tmp_path, "head")
    with engine.begin() as conn:
        seed_graph(conn, "gr_1", at=now)
    store = hub_store_connections(engine)
    chunks = build_chunk_stores(store, FixedClock(now), registry=RunnerRegistryStore(store))
    for chunk_id in ("ch_a", "ch_b", "ch_c"):
        seed_chunk_record(chunks, Chunk(chunk_id=chunk_id, graph_id="gr_1", work_refs=[], minted_at=now))
    with chunks.exclusive.locked(["ch_a", "ch_b", "ch_c"]) as locked:
        chunks.dependencies.declare_locked(locked, "ch_a", "ch_b", by="test", at=now)
        chunks.dependencies.declare_locked(locked, "ch_a", "ch_c", by="test", at=now)
    service = tx.HubTransactions(store)
    with pytest.raises(ValueError), service.begin() as unit:
        assert chunks.record.get("ch_a", transaction=unit) is not None
        assert chunks.facts.load_facts("ch_a", transaction=unit) is not None
        assert chunks.dependencies.release("ch_a", "ch_b", by="test", at=now, transaction=unit) is not None
        assert len(chunks.dependencies.standing_edges_for("ch_a", transaction=unit)) == 1
        raise ValueError("abort")
    assert len(chunks.dependencies.standing_edges_for("ch_a")) == 2
    with service.begin() as unit:
        assert chunks.dependencies.release("ch_a", "ch_b", by="test", at=now, transaction=unit) is not None
        assert chunks.dependencies.release("ch_a", "ch_c", by="test", at=now, transaction=unit) is not None
    assert chunks.dependencies.standing_edges_for("ch_a") == []
