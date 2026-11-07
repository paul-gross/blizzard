from pathlib import Path

import pytest

from blizzard.hub.domain.graph.model import Graph
from blizzard.wire.route import PinnedGraphMissingProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches, unobserved_variants
from tests.support.scaffolding import build_hub, ingest, seed_runner


@pytest.mark.parametrize("operation", ["hub-advance", "completions", "decisions"])
def test_missing_pinned_graph_refuses_runner_operation_without_mutation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, operation: str
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    token = seed_runner(hub, "r1")
    chunk = ingest(hub, [{"source": "default", "ref": "missing-graph"}])
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []},
        headers=bearer(token),
    )
    assert claim.status_code == 201, claim.text
    record = hub.services.chunks.record.get(chunk)
    assert record is not None
    graphs = hub.services.graphs
    get_graph = graphs.get
    assert get_graph(record.graph_id) is not None
    before_facts = hub.services.chunks.facts.load_facts(chunk)
    before_route = hub.services.chunks.route.route_of(chunk)
    before_events = list(hub.events.snapshot())

    def missing_graph(graph_id: str) -> Graph | None:
        return None if graph_id == record.graph_id else get_graph(graph_id)

    monkeypatch.setattr(graphs, "get", missing_graph)
    url = f"/api/fleet/chunks/{chunk}/{operation}"
    path = f"/api/fleet/chunks/{{chunk_id}}/{operation}"
    response = hub.client.post(
        url,
        headers=bearer(token),
        json={"epoch": 1, "from_node_id": "nd_1", "choice": "done", "route_token": claim.json()["route_token"]},
    )

    assert response.status_code == 500, response.text
    assert response.json()["detail"] == "chunk's pinned graph is missing"
    assert response.json()["instance"] == url
    observation = RefusalObservation(
        "post", path, 500, response.headers["content-type"], PinnedGraphMissingProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(
            hub.app.openapi(), "post", path, 500, "application/problem+json", {PinnedGraphMissingProblem}
        )
        == set()
    )
    assert hub.services.chunks.record.get(chunk) == record
    assert hub.services.chunks.facts.load_facts(chunk) == before_facts
    assert hub.services.chunks.route.route_of(chunk) == before_route
    assert hub.events.snapshot() == before_events
