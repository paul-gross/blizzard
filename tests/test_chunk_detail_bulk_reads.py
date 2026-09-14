"""``GET /chunks/{id}`` — the detail route's graph-name priming (component tier,
blizzard#515/#518 Phase 2).

Proves the route primes every graph id its history/restarts/migrations/intended
migration name, so it never reaches `IReadGraphRepository.get` except the one
conditional reify `_pending` still needs for a node's poll policy."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.hub.domain.graph import Graph
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal.graph_store import GraphStore
from tests.support import build_hub, hub_store_connections, ingest

pytestmark = pytest.mark.component


class _CountingGraphStore(GraphStore):
    """Counts calls to the fully-reifying `get`, so a test can pin that the detail
    route reaches it only through `_pending`'s own conditional path."""

    def __init__(self, store: HubStoreConnections) -> None:
        super().__init__(store)
        self.get_calls = 0

    def get(self, graph_id: str) -> Graph | None:
        self.get_calls += 1
        return super().get(graph_id)


def test_chunk_detail_never_calls_graphs_get_with_no_pending_poll(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_id = ingest(hub, [{"source": "default", "ref": "1"}])

    counting_graphs = _CountingGraphStore(hub_store_connections(hub.engine))
    assert hub.app is not None
    hub.app.state.services = replace(hub.services, graphs=counting_graphs)

    resp = hub.client.get(f"/api/chunks/{chunk_id}")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["pending"] is None
    assert body["graph_name"] is not None
    assert body["current_node_name"] is not None
    assert counting_graphs.get_calls == 0
