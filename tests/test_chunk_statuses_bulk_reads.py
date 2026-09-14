"""``GET /api/fleet/chunk-statuses`` — the runner tick's slim batch status read (component
tier, blizzard#521).

Proves the route reads the requested chunks' facts, routes, and live decisions with one
bulk-by-id-set query each, so the query count stays flat as the batch grows and never
reaches ``load_facts``/``route_of``/``decision_for_chunk`` at all — and that an id
unknown to the store is silently omitted rather than answered with a 404."""

from __future__ import annotations

from dataclasses import replace
from pathlib import Path

import pytest

from blizzard.foundation.clock import IClock
from blizzard.hub.domain.chunks.decisions import LiveDecisionStatus
from blizzard.hub.domain.fleet import Route
from blizzard.hub.domain.work import ChunkFacts, DecisionRow
from blizzard.hub.store.errors import HubStoreConnections
from blizzard.hub.store.internal import batching as batching_module
from blizzard.hub.store.internal.chunk_decisions_store import ChunkDecisionsStore
from blizzard.hub.store.internal.chunk_facts_store import ChunkFactsStore
from blizzard.hub.store.internal.chunk_route_store import ChunkRouteStore
from tests.support import build_hub, count_queries, hub_store_connections, ingest, report_lease

pytestmark = pytest.mark.component

_YAML = """
name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build it.
    judgement:
      prompt: Assess the build.
      choices:
        pass:
          description: Complete.
          to: approve-gate
        fail:
          description: Incomplete.
          to: build
  approve-gate:
    executor: runner
    judgement:
      by: human
      choices:
        approve:
          description: Ship it.
          to: done
        reject:
          description: Send back.
          to: build
"""


def _usage_payload(node_id: str, chunk_id: str) -> dict:
    return {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "kind": "spawn",
        "model": "m",
        "input_tokens": 10,
        "output_tokens": 5,
        "cache_read_tokens": 1,
        "cache_create_tokens": 1,
        "cost_usd": 0.25,
    }


def _seed_batch(hub, n: int) -> list[str]:  # type: ignore[no-untyped-def]
    """``n`` claimed chunks, cycling every fourth one through a pause, a restart, an
    open gate decision, and a recorded usage fact — so a batch of any size exercises
    every field the tick loop reads."""
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _YAML})
    assert graph.status_code == 201, graph.text
    build_node_id = next(n["node_id"] for n in graph.json()["nodes"] if n["name"] == "build")

    chunk_ids: list[str] = []
    for i in range(n):
        chunk_id = ingest(hub, [{"source": "default", "ref": str(i)}])
        runner_id = f"r{i}"
        claimed = hub.client.post(
            "/api/fleet/routes",
            json={"chunk_id": chunk_id, "runner_id": runner_id, "workspace_id": f"w{i}", "environment_ids": ["e1"]},
        )
        assert claimed.status_code == 201, claimed.text
        report_lease(hub, chunk_id, epoch=1, seq=1, runner_id=runner_id)

        role = i % 4
        if role == 0:
            assert hub.client.post(f"/api/fleet/chunks/{chunk_id}/pause").status_code == 202
        elif role == 1:
            assert hub.client.post(f"/api/chunks/{chunk_id}/restart", json={}).status_code == 202
        elif role == 2:
            resp = hub.client.post(
                f"/api/fleet/chunks/{chunk_id}/completions",
                json={
                    "choice": "pass",
                    "epoch": 1,
                    "runner_id": runner_id,
                    "from_node_id": build_node_id,
                    "artifacts": [],
                },
            )
            assert resp.status_code == 200, resp.text
        else:
            resp = hub.client.post(
                "/api/fleet/events",
                json={
                    "runner_id": runner_id,
                    "facts": [{"seq": 2, "kind": "usage.recorded", "payload": _usage_payload(build_node_id, chunk_id)}],
                },
            )
            assert resp.status_code == 200, resp.text
        chunk_ids.append(chunk_id)
    return chunk_ids


def test_chunk_statuses_query_count_is_independent_of_batch_size(tmp_path: Path) -> None:
    (tmp_path / "small").mkdir()
    (tmp_path / "large").mkdir()
    small = build_hub(tmp_path / "small")
    small_ids = _seed_batch(small, 4)
    large = build_hub(tmp_path / "large")
    large_ids = _seed_batch(large, 12)  # 3x the small batch

    results: dict[str, int] = {}

    def call(hub, ids: list[str], key: str) -> None:  # type: ignore[no-untyped-def]
        resp = hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": ids})
        assert resp.status_code == 200, resp.text
        results[key] = len(resp.json())

    small_count = count_queries(small.engine, lambda: call(small, small_ids, "small"))
    large_count = count_queries(large.engine, lambda: call(large, large_ids, "large"))

    assert results == {"small": 4, "large": 12}
    assert small_count == large_count


class _CountingFactsStore(ChunkFactsStore):
    """Counts calls to the bulk-by-id-set and per-chunk facts reads."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.status_facts_for_calls = 0
        self.load_facts_calls = 0

    def status_facts_for(self, chunk_ids) -> dict[str, ChunkFacts]:  # type: ignore[no-untyped-def]
        self.status_facts_for_calls += 1
        return super().status_facts_for(chunk_ids)

    def load_facts(self, chunk_id: str) -> ChunkFacts | None:
        self.load_facts_calls += 1
        return super().load_facts(chunk_id)


class _CountingRouteStore(ChunkRouteStore):
    """Counts calls to the bulk-by-id-set and per-chunk route reads."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.routes_for_calls = 0
        self.route_of_calls = 0

    def routes_for(self, chunk_ids) -> dict[str, Route]:  # type: ignore[no-untyped-def]
        self.routes_for_calls += 1
        return super().routes_for(chunk_ids)

    def route_of(self, chunk_id: str) -> Route | None:
        self.route_of_calls += 1
        return super().route_of(chunk_id)


class _CountingDecisionsStore(ChunkDecisionsStore):
    """Counts calls to the bulk-by-id-set and per-chunk live-decision reads."""

    def __init__(self, store: HubStoreConnections, clock: IClock) -> None:
        super().__init__(store, clock)
        self.live_decisions_for_calls = 0
        self.decision_for_chunk_calls = 0

    def live_decisions_for(self, chunk_ids) -> dict[str, LiveDecisionStatus]:  # type: ignore[no-untyped-def]
        self.live_decisions_for_calls += 1
        return super().live_decisions_for(chunk_ids)

    def decision_for_chunk(self, chunk_id: str) -> DecisionRow | None:
        self.decision_for_chunk_calls += 1
        return super().decision_for_chunk(chunk_id)


def test_chunk_statuses_calls_bulk_reads_and_never_the_per_chunk_ones(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk_ids = _seed_batch(hub, 4)

    counting_facts = _CountingFactsStore(hub_store_connections(hub.engine), hub.clock)
    counting_route = _CountingRouteStore(hub_store_connections(hub.engine), hub.clock)
    counting_decisions = _CountingDecisionsStore(hub_store_connections(hub.engine), hub.clock)
    assert hub.app is not None
    hub.app.state.services = replace(
        hub.services,
        chunks=replace(hub.services.chunks, facts=counting_facts, route=counting_route, decisions=counting_decisions),
    )

    resp = hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": chunk_ids})

    assert resp.status_code == 200, resp.text
    assert len(resp.json()) == len(chunk_ids)
    assert counting_facts.status_facts_for_calls == 1
    assert counting_route.routes_for_calls == 1
    assert counting_decisions.live_decisions_for_calls == 1
    assert counting_facts.load_facts_calls == 0
    assert counting_route.route_of_calls == 0
    assert counting_decisions.decision_for_chunk_calls == 0


def test_chunk_statuses_correct_across_a_lowered_batch_size_boundary(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Every sibling ``*_bulk_reads.py`` test pins a lowered ``batching.BATCH_SIZE`` to
    prove its bulk read actually batches rather than splicing every id into one
    unbounded ``IN (...)`` — this is that test for the route's own three bulk-by-id-set
    reads (facts, routes, live decisions), each exercised through every role
    ``_seed_batch`` cycles a chunk through."""
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    hub = build_hub(tmp_path)
    chunk_ids = _seed_batch(hub, 7)  # 3 batches of size 3, 3, 1 under the lowered cap

    resp = hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": chunk_ids})

    assert resp.status_code == 200, resp.text
    views_by_id = {v["chunk_id"]: v for v in resp.json()}
    assert set(views_by_id) == set(chunk_ids)
    for i, chunk_id in enumerate(chunk_ids):
        role = i % 4
        view = views_by_id[chunk_id]
        if role == 0:
            assert view["pause"] is not None
        elif role == 1:
            assert len(view["restart_epochs"]) == 1
        elif role == 2:
            assert view["decision"] is not None
        else:
            assert view["cost"]["cost_usd"] == pytest.approx(0.25)


def test_an_unknown_chunk_id_is_silently_omitted(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    [chunk_id] = _seed_batch(hub, 1)

    resp = hub.client.get("/api/fleet/chunk-statuses", params={"chunk_id": [chunk_id, "ch_nope"]})

    assert resp.status_code == 200, resp.text
    assert [v["chunk_id"] for v in resp.json()] == [chunk_id]
