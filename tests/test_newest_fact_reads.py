"""``bzh:live-set-read`` — a newest-fact read costs rows in the number of keys, not in each
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
from blizzard.hub.store.internal.graph_store import GraphStore
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
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


def test_newest_fact_reads_are_flat_across_history_depth(tmp_path: Path) -> None:
    shallow = _reads(_engine(tmp_path / "shallow", depth=1))
    deep_engine = _engine(tmp_path / "deep", depth=6)
    deep = _reads(deep_engine)
    for name in shallow:
        assert shallow[name]() == deep[name](), name  # type: ignore[operator]
        assert count_rows_read(deep_engine, deep[name]) == len(_KEYS), name  # type: ignore[arg-type]
