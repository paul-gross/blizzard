"""``ChunkFactsStore.restarted_past`` answers what ``ChunkFacts.restarted_past`` answers over
``load_facts``, without loading it (component tier)."""

from __future__ import annotations

from pathlib import Path

from blizzard.hub.domain.chunk.model import ChunkFacts
from blizzard.hub.domain.chunk.ports.facts import IReadChunkFactsRepository
from blizzard.hub.store import schema as s
from blizzard.hub.store.internal.chunk_rows import record_deleted_row, record_grouped_row_conn
from blizzard.hub.store.scoped import ScopedConnections
from tests.support.scaffolding import (
    HubHarness,
    add_tenant,
    build_hub,
    hub_store_connections,
    ingest,
    scoped_connections,
)


def _restart(hub: HubHarness, conns: ScopedConnections, chunk_id: str, *epochs: int) -> None:
    with conns.write("seed") as conn:
        for epoch in epochs:
            conn.execute(
                conns.insert(s.chunk_restarts).values(
                    chunk_id=chunk_id,
                    graph_id="gr_1",
                    to_node_id="nd_1",
                    epoch=epoch,
                    restarted_by="op",
                    recorded_at=hub.clock.now(),
                )
            )


def test_the_probe_agrees_with_the_projection(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    conns = hub_store_connections(hub.engine)
    facts = hub.default_adapter(IReadChunkFactsRepository)
    never, same, above, several, deleted, grouped = (
        ingest(hub, [{"source": "default", "ref": str(i)}]) for i in range(6)
    )
    _restart(hub, conns, same, 2)
    _restart(hub, conns, above, 3)
    _restart(hub, conns, several, 1, 4, 2)
    _restart(hub, conns, deleted, 5)
    _restart(hub, conns, grouped, 5)
    with conns.write("seed") as conn:
        record_deleted_row(conn, deleted, by="op", at=hub.clock.now())
        record_grouped_row_conn(conn, grouped, grouped_into=never, at=hub.clock.now())

    expected = {
        never: False,
        same: False,
        above: True,
        several: True,
        deleted: False,
        grouped: False,
        "ch_unknown": False,
    }
    for chunk_id, answer in expected.items():
        assert facts.restarted_past(chunk_id, 2) is answer, chunk_id
        assert ChunkFacts.or_default(facts.load_facts(chunk_id)).restarted_past(2) is answer, chunk_id


def test_another_tenants_restart_does_not_count(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])
    facts = hub.default_adapter(IReadChunkFactsRepository)
    other = add_tenant(hub.engine, "other")
    _restart(hub, scoped_connections(hub.engine, other), chunk_id, 9)

    assert facts.restarted_past(chunk_id, 1) is False
