"""``bzh:newest-per-key-read`` — a newest-fact read costs rows in the number of keys, not in each
key's fact history (component tier).

Each read answers the same key set at two history depths: the answer is the newest fact and
the rows fetched are identical."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest
from sqlalchemy import Engine, insert

from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_queue_store import ChunkQueueStore
from blizzard.hub.store.internal.egress_store import EgressStore
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.repository_record_store import _retirements
from blizzard.hub.store.internal.routine_store import RoutineStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from blizzard.hub.store.internal.scope_store import ScopeStore
from blizzard.hub.store.internal.secret_store import SecretStore
from blizzard.hub.store.internal.trace_store import TraceStore
from blizzard.hub.store.internal.work_source_record_store import _retired_names
from tests.support import build_hub, count_rows_read, hub_store_connections

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
                    insert(s.queue_positions).values(chunk_id=key, position=7.0 if last else float(n), set_at=_T0)
                )
                conn.execute(insert(s.runner_pause_facts).values(runner_id=key, paused=last, set_at=_T0, set_by="op"))
                conn.execute(
                    insert(s.runner_local_pause_facts).values(
                        runner_id=key, paused=last, set_at=_T0, set_by="op", reason="cause" if last else f"stale{n}"
                    )
                )
                conn.execute(
                    insert(s.runner_lifecycle_facts).values(runner_id=key, retired=last, set_at=_T0, set_by="op")
                )
                conn.execute(
                    insert(s.graph_lifecycle_facts).values(graph_id=key, retired=last, set_at=_T0, set_by="op")
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
                conn.execute(insert(s.secret_lifecycle_facts).values(name=key, **common))
                conn.execute(insert(s.routine_lifecycle_facts).values(routine_id=key, **common))
                conn.execute(insert(s.scope_lifecycle_facts).values(slug=key, **common))
                conn.execute(insert(s.repository_lifecycle_facts).values(name=key, **common))
                conn.execute(insert(s.work_source_lifecycle_facts).values(name=key, **common))
                conn.execute(insert(s.graph_lifecycle_facts).values(graph_id=key, **common))
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
                    recorded_at=_T0.replace(minute=n), severity="warning", kind=kind, message=f"m{n}"
                )
            )
    store = hub_store_connections(engine)

    egress = EgressStore(store).newest_egress_failure()
    export = TraceStore(store, graphs=None, names=None, label=None).newest_export_failure()  # type: ignore[arg-type]

    assert egress is not None and egress.message == "m0"
    assert export is not None and export.message == "m2"
    assert count_rows_read(engine, lambda: EgressStore(store).newest_egress_failure()) <= 1
