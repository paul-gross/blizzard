"""``bzh:newest-per-key-read`` — a newest-fact read costs rows in the number of keys, not in each
key's fact history (component tier).

Each read answers the same key set at two history depths: the answer is the newest fact and
the rows fetched are identical."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Boolean, Column, Engine, Integer, MetaData, String, Table, create_engine, insert
from structlog import get_logger

from blizzard.hub.store import schema as s
from blizzard.hub.store.errors import HubStoreErrorFactory
from blizzard.hub.store.internal.chunk_queue_store import ChunkQueueStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.newest_fact import newest_fact_select, newest_retired_select
from blizzard.hub.store.internal.repository_record_store import _retirements
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.trace_store import TraceStore
from blizzard.hub.store.internal.work_source_record_store import _retired_names
from blizzard.hub.store.scoped import ScopedConnections
from blizzard.hub.tenancy import StoreScope, TenantId
from tests.support import build_hub, count_rows_read, hub_store_connections, stamp

pytestmark = pytest.mark.component

_T0 = datetime(2026, 1, 1, tzinfo=UTC)
_KEYS = ["k1", "k2", "k3"]


def _engine(tmp_path: Path, *, depth: int) -> Engine:
    """An engine holding ``depth`` facts per key in every fact table; the last is the newest."""
    tmp_path.mkdir()
    engine = build_hub(tmp_path).engine
    with engine.begin() as conn:
        for n in range(depth):
            last = n == depth - 1
            for key in _KEYS:
                conn.execute(
                    insert(s.queue_positions).values(
                        stamp(s.queue_positions, chunk_id=key, position=7.0 if last else float(n), set_at=_T0)
                    )
                )
                conn.execute(
                    insert(s.runner_pause_facts).values(
                        stamp(s.runner_pause_facts, runner_id=key, paused=last, set_at=_T0, set_by="op")
                    )
                )
                conn.execute(
                    insert(s.runner_local_pause_facts).values(
                        stamp(
                            s.runner_local_pause_facts,
                            runner_id=key,
                            paused=last,
                            set_at=_T0,
                            set_by="op",
                            reason="cause" if last else f"stale{n}",
                        )
                    )
                )
                conn.execute(
                    insert(s.runner_lifecycle_facts).values(
                        stamp(s.runner_lifecycle_facts, runner_id=key, retired=last, set_at=_T0, set_by="op")
                    )
                )
                conn.execute(
                    insert(s.graph_lifecycle_facts).values(
                        stamp(s.graph_lifecycle_facts, graph_id=key, retired=last, set_at=_T0, set_by="op")
                    )
                )
    return engine


def _reads(engine: Engine) -> dict[str, object]:
    store = hub_store_connections(engine)

    def registry(fn):  # type: ignore[no-untyped-def]
        def run() -> object:
            with store.read("test") as conn:
                return fn(conn, _KEYS)

        return run

    return {
        "queue_positions": lambda: ChunkQueueStore(store, None).queue_positions(_KEYS),  # type: ignore[arg-type]
        "paused": registry(RunnerRegistryStore._paused_many),
        "local_pause": registry(RunnerRegistryStore._local_pause_detail_many),
        "lifecycle": registry(RunnerRegistryStore._lifecycle_many),
        "graph_retired": registry(lambda conn, keys: GraphStore(store)._retired_among(conn, keys)),
    }


def _retirement_engine(tmp_path: Path, *, depth: int) -> Engine:
    """``depth`` alternating retired/enabled facts per key in every configured-record lifecycle
    table, the last one retired for ``k1`` and ``k3`` and enabled for ``k2``."""
    tmp_path.mkdir()
    engine = build_hub(tmp_path).engine
    with engine.begin() as conn:
        for n in range(depth):
            for key in _KEYS:
                last_retired = key != "k2"
                retired = last_retired if n == depth - 1 else not last_retired
                common = {"retired": retired, "set_at": _T0, "set_by": "op"}
                conn.execute(
                    insert(s.secret_lifecycle_facts).values(stamp(s.secret_lifecycle_facts, name=key, **common))
                )
                conn.execute(
                    insert(s.routine_lifecycle_facts).values(stamp(s.routine_lifecycle_facts, routine_id=key, **common))
                )
                conn.execute(insert(s.scope_lifecycle_facts).values(stamp(s.scope_lifecycle_facts, slug=key, **common)))
                conn.execute(
                    insert(s.repository_lifecycle_facts).values(stamp(s.repository_lifecycle_facts, name=key, **common))
                )
                conn.execute(
                    insert(s.work_source_lifecycle_facts).values(
                        stamp(s.work_source_lifecycle_facts, name=key, **common)
                    )
                )
                conn.execute(
                    insert(s.graph_lifecycle_facts).values(stamp(s.graph_lifecycle_facts, graph_id=key, **common))
                )
    return engine


def _retirement_reads(engine: Engine) -> dict[str, object]:
    store = hub_store_connections(engine)
    return {
        "secret": lambda: SecretStore(store).retired_names(),
        "routine": lambda: RoutineStore(store).retired_ids(),
        "scope": lambda: ScopeStore(store).retired_slugs(),
        "graph": lambda: GraphStore(store).retired_graph_ids(),
        "repository": lambda: _read(store, lambda conn: _retirements(conn)),
        "repository_bounded": lambda: _read(store, lambda conn: _retirements(conn, ["k1", "k2"])),
        "work_source": lambda: _read(store, lambda conn: _retired_names(conn)),
        "work_source_bounded": lambda: _read(store, lambda conn: _retired_names(conn, ["k1", "k2"])),
    }


def _read(store, fn):  # type: ignore[no-untyped-def]
    with store.read("test") as conn:
        return fn(conn)


def test_retirement_reads_are_flat_across_history_depth(tmp_path: Path) -> None:
    shallow = _retirement_reads(_retirement_engine(tmp_path / "shallow", depth=1))
    deep_engine = _retirement_engine(tmp_path / "deep", depth=6)
    deep = _retirement_reads(deep_engine)
    for name in shallow:
        bounded = name.endswith("bounded")
        answer = deep[name]()  # type: ignore[operator]
        assert answer == shallow[name](), name  # type: ignore[operator]
        assert len(answer) == (1 if bounded else 2), name  # type: ignore[arg-type]
        assert count_rows_read(deep_engine, deep[name]) <= (2 if bounded else len(_KEYS)), name  # type: ignore[arg-type]


def test_newest_fact_reads_are_flat_across_history_depth(tmp_path: Path) -> None:
    shallow = _reads(_engine(tmp_path / "shallow", depth=1))
    deep_engine = _engine(tmp_path / "deep", depth=6)
    deep = _reads(deep_engine)
    for name in shallow:
        assert shallow[name]() == deep[name](), name  # type: ignore[operator]
        assert count_rows_read(deep_engine, deep[name]) == len(_KEYS), name  # type: ignore[arg-type]


def test_newest_failure_reads_keep_the_old_failure_after_a_recovery(tmp_path: Path) -> None:
    engine = build_hub(tmp_path).engine
    with engine.begin() as conn:
        for n, kind in enumerate(
            ["egress-write-failed", "egress-write-recovered", "trace-export-failed", "trace-export-recovered"]
        ):
            conn.execute(
                insert(s.event_log).values(
                    stamp(
                        s.event_log, recorded_at=_T0.replace(minute=n), severity="warning", kind=kind, message=f"m{n}"
                    )
                )
            )
    store = hub_store_connections(engine)

    egress = EgressStore(store).newest_egress_failure()
    export = TraceStore(store, graphs=None, names=None, label=None).newest_export_failure()  # type: ignore[arg-type]

    assert egress is not None and egress.message == "m0"
    assert export is not None and export.message == "m2"
    assert count_rows_read(engine, lambda: EgressStore(store).newest_egress_failure()) <= 1


# --- the newest-fact read is confined to its scope, inner group-by included -----------------------

_TEN_A = TenantId("ten_A")
_TEN_B = TenantId("ten_B")
_md = MetaData()
# A stand-in lifecycle fact table carrying the tenant key the live schema does not have yet.
_facts = Table(
    "secret_lifecycle_facts",
    _md,
    Column("id", Integer, primary_key=True, autoincrement=True),
    Column("tenant_id", String, nullable=False),
    Column("name", String, nullable=False),
    Column("retired", Boolean, nullable=False),
)


def _two_tenant_facts(tmp_path: Path) -> Engine:
    """Two tenants sharing the key ``k``: A retired it then restored it; B's newer fact retires it.
    A group-by left hub-wide would pick B's id, and A's own newest fact would vanish."""
    engine = create_engine(f"sqlite:///{tmp_path / 'facts.db'}")
    _md.create_all(engine)
    with engine.begin() as conn:
        for tenant, retired in ((_TEN_A, True), (_TEN_A, False), (_TEN_B, False), (_TEN_B, True)):
            conn.execute(insert(_facts).values(tenant_id=tenant, name="k", retired=retired))
        conn.execute(insert(_facts).values(tenant_id=_TEN_B, name="b_only", retired=True))
    return engine


@pytest.mark.parametrize("keys", [None, ["k", "b_only"]], ids=["every_key", "bounded"])
def test_the_newest_fact_read_answers_only_from_its_scope_facts(tmp_path: Path, keys: list[str] | None) -> None:
    engine = _two_tenant_facts(tmp_path)
    errors = HubStoreErrorFactory(get_logger("test"))
    a = ScopedConnections(engine, errors, StoreScope(_TEN_A))
    b = ScopedConnections(engine, errors, StoreScope(_TEN_B))
    with a.read("test") as conn:
        newest_a = conn.execute(newest_fact_select(a, _facts, _facts.c.name, keys, _facts.c.name, _facts.c.retired))
        assert [tuple(r) for r in newest_a] == [("k", False)]
        assert set(conn.execute(newest_retired_select(a, _facts, _facts.c.name, keys)).scalars()) == set()
    with b.read("test") as conn:
        assert set(conn.execute(newest_retired_select(b, _facts, _facts.c.name, keys)).scalars()) == {"k", "b_only"}
