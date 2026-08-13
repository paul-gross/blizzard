"""The analytics spend routes (blizzard#256, Phase 3, component tier): the
TRANSCRIPT_READ auth triad, real usage/cost rollups by node, graph, and chunk, every
shared filter (D7), cursor paging and NDJSON parity for the per-chunk grouping, and the
reconciliation test against ``UsageTotal.of`` and ``GET /api/spend`` (D6)."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.auth_core import Role
from blizzard.hub.api.analytics import ScopeFilters, chunk_spend_ndjson_lines
from blizzard.hub.config import RUNNER_AUTH_ENFORCE
from blizzard.hub.domain.work import UsageFact, UsageTotal
from tests.support import FakeWorkSource, build_hub, pointer_token, seed_session, seed_user
from tests.test_fleet_auth import _seed_enrolled

pytestmark = pytest.mark.component

#: Every route this module holds to the auth triad — one list, three sweeps over it.
_ROUTES = [
    "/api/analytics/spend/nodes",
    "/api/analytics/spend/graphs",
    "/api/analytics/spend/chunks",
    "/api/analytics/spend/chunks/ndjson",
]

#: Named to match the packaged default graph's own name, so `POST /api/chunks` mints
#: onto *this* graph — see `tests/test_analytics_durations_api.py`'s own note.
_GRAPH_YAML = """
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
          to: review
        fail:
          description: Incomplete.
          to: build
  review:
    executor: runner
    prompt: Review it.
    judgement:
      prompt: Assess the review.
      choices:
        pass:
          description: Complete.
          to: done
"""


def _cookie(token: str) -> dict[str, str]:
    return {"Cookie": f"bz_session={token}"}


def _bearer(token: str) -> dict[str, str]:
    return {"Authorization": f"Bearer {token}"}


def _seeded_hub(tmp_path: Path, *, work_sources: dict[str, FakeWorkSource] | None = None):  # type: ignore[no-untyped-def]
    hub = build_hub(tmp_path, auth_mode="oauth", work_sources=work_sources)
    admin = seed_user(hub, username="admin", role=Role.ADMIN)
    admin_token = seed_session(hub, admin)
    graph = hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH_YAML}, headers=_cookie(admin_token))
    assert graph.status_code == 201, graph.text
    graph_id = graph.json()["graph_id"]
    nodes = {n["name"]: n["node_id"] for n in graph.json()["nodes"]}

    contributor = seed_user(hub, username="ada", role=Role.CONTRIBUTOR)
    token = seed_session(hub, contributor)
    return hub, token, graph_id, nodes


def _mint_chunk(hub, token: str, *, source: str = "default", ref: str = "1") -> str:  # type: ignore[no-untyped-def]
    resp = hub.client.post(
        "/api/chunks", json={"tokens": [pointer_token({"source": source, "ref": ref})]}, headers=_cookie(token)
    )
    assert resp.status_code == 201, resp.text
    return str(resp.json()["chunk_id"])


def _push_usage(  # type: ignore[no-untyped-def]
    hub, *, chunk_id: str, node_id: str, epoch: int, seq: int, cost_usd: float | None
) -> None:
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": epoch,
        "kind": "spawn",
        "model": "claude-opus-4-8",
        "input_tokens": 100,
        "output_tokens": 50,
        "cache_read_tokens": 10,
        "cache_create_tokens": 5,
        "cost_usd": cost_usd,
    }
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


# --- auth triad: 401 / 403 / 200, plus the runner-principal refusal ---------------


@pytest.mark.parametrize("path", _ROUTES)
def test_every_route_is_401_with_no_session(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    resp = hub.client.get(path)
    assert resp.status_code == 401


@pytest.mark.parametrize("path", _ROUTES)
def test_every_route_is_403_below_transcript_read(tmp_path: Path, path: str) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = seed_user(hub, username="grace", role=Role.GUEST)
    token = seed_session(hub, guest)

    resp = hub.client.get(path, headers=_cookie(token))
    assert resp.status_code == 403


@pytest.mark.parametrize("path", _ROUTES)
def test_every_route_refuses_a_runner_principal(tmp_path: Path, path: str) -> None:
    token = _seed_enrolled(tmp_path, runner_id="runner-a")
    hub = build_hub(tmp_path, auth_mode="oauth", runner_auth_mode=RUNNER_AUTH_ENFORCE)

    resp = hub.client.get(path, headers=_bearer(token))
    assert resp.status_code == 403


# --- real rollups, grouped by node, by graph, and by chunk ---------------------------


def test_spend_by_node_rolls_up_usage(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.2)

    resp = hub.client.get("/api/analytics/spend/nodes", headers=_cookie(token))

    assert resp.status_code == 200, resp.text
    assert resp.json()["spend"] == [
        {
            "key": nodes["build"],
            "input_tokens": 200,
            "output_tokens": 100,
            "cache_read_tokens": 20,
            "cache_create_tokens": 10,
            "cost_usd": pytest.approx(0.3),
            "cost_partial": False,
        }
    ]


def test_spend_by_graph_groups_on_the_chunks_current_graph(tmp_path: Path) -> None:
    hub, token, graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)

    resp = hub.client.get("/api/analytics/spend/graphs", headers=_cookie(token))

    assert resp.json()["spend"] == [
        {
            "key": graph_id,
            "input_tokens": 100,
            "output_tokens": 50,
            "cache_read_tokens": 10,
            "cache_create_tokens": 5,
            "cost_usd": pytest.approx(0.1),
            "cost_partial": False,
        }
    ]


def test_a_null_cost_row_sums_tokens_and_flags_the_group_partial(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=2, cost_usd=None)

    resp = hub.client.get("/api/analytics/spend/nodes", headers=_cookie(token))

    row = resp.json()["spend"][0]
    assert row["cost_usd"] == pytest.approx(0.1)  # the null row is skipped, never read as zero
    assert row["cost_partial"] is True
    assert row["input_tokens"] == 200  # token counts are exact regardless of cost


# --- per-chunk: cursor paging and NDJSON parity ---------------------------------------


def test_spend_by_chunk_pages_with_a_cursor(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_a = _mint_chunk(hub, token, ref="1")
    chunk_b = _mint_chunk(hub, token, ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.1)

    first = hub.client.get("/api/analytics/spend/chunks", params={"limit": 1}, headers=_cookie(token))
    assert first.status_code == 200, first.text
    assert len(first.json()["spend"]) == 1
    cursor = first.json()["next_cursor"]
    assert cursor is not None

    second = hub.client.get(
        "/api/analytics/spend/chunks", params={"limit": 1, "cursor": cursor}, headers=_cookie(token)
    )
    assert len(second.json()["spend"]) == 1
    assert second.json()["next_cursor"] is None
    assert {first.json()["spend"][0]["chunk_id"], second.json()["spend"][0]["chunk_id"]} == {chunk_a, chunk_b}


@pytest.mark.parametrize("cursor", ["not-a-chunk-id", "", "ch"])
def test_spend_by_chunk_422s_on_a_malformed_cursor(tmp_path: Path, cursor: str) -> None:
    hub, token, _graph_id, _nodes = _seeded_hub(tmp_path)

    resp = hub.client.get("/api/analytics/spend/chunks", params={"cursor": cursor}, headers=_cookie(token))

    assert resp.status_code == 422, resp.text
    assert resp.json()["detail"] == "malformed cursor"


def test_ndjson_serves_the_same_chunk_spend_in_the_same_order_as_json(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_a = _mint_chunk(hub, token, ref="1")
    chunk_b = _mint_chunk(hub, token, ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.1)

    json_resp = hub.client.get("/api/analytics/spend/chunks", headers=_cookie(token))
    ndjson_resp = hub.client.get("/api/analytics/spend/chunks/ndjson", headers=_cookie(token))

    assert ndjson_resp.status_code == 200, ndjson_resp.text
    assert ndjson_resp.headers["content-type"].startswith("application/x-ndjson")
    lines = [json.loads(line) for line in ndjson_resp.text.strip().split("\n")]
    assert [r["chunk_id"] for r in lines] == [r["chunk_id"] for r in json_resp.json()["spend"]]


def test_the_ndjson_stream_carries_its_cursor_across_batches(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_a = _mint_chunk(hub, token, ref="1")
    chunk_b = _mint_chunk(hub, token, ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.1)
    criteria = ScopeFilters(None, None, None, None).criteria()

    body = b"".join(chunk_spend_ndjson_lines(hub.services.operational_analytics, criteria, batch_size=1)).decode()
    lines = [json.loads(line) for line in body.splitlines()]

    whole = hub.client.get("/api/analytics/spend/chunks", headers=_cookie(token)).json()["spend"]
    assert [r["chunk_id"] for r in lines] == [r["chunk_id"] for r in whole]
    assert len(lines) == 2


# --- the shared filter vocabulary (D7) -----------------------------------------------


def test_spend_honors_the_source_filter(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(
        tmp_path, work_sources={"default": FakeWorkSource(), "other": FakeWorkSource(name="other")}
    )
    chunk_a = _mint_chunk(hub, token, source="default", ref="1")
    chunk_b = _mint_chunk(hub, token, source="other", ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.2)

    resp = hub.client.get("/api/analytics/spend/chunks", params={"source": "other"}, headers=_cookie(token))

    assert [r["chunk_id"] for r in resp.json()["spend"]] == [chunk_b]


def test_spend_honors_the_graph_id_filter(tmp_path: Path) -> None:
    hub, token, graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)

    resp = hub.client.get("/api/analytics/spend/nodes", params={"graph_id": graph_id}, headers=_cookie(token))
    assert len(resp.json()["spend"]) == 1

    resp_other = hub.client.get(
        "/api/analytics/spend/nodes", params={"graph_id": "gr_nonexistent"}, headers=_cookie(token)
    )
    assert resp_other.json()["spend"] == []


def test_spend_honors_the_time_range_filter(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_id = _mint_chunk(hub, token)
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    hub.clock.advance(timedelta(seconds=1))
    after_first = hub.clock.now().isoformat()
    _push_usage(hub, chunk_id=chunk_id, node_id=nodes["build"], epoch=1, seq=2, cost_usd=0.2)

    resp = hub.client.get("/api/analytics/spend/nodes", params={"since": after_first}, headers=_cookie(token))

    assert resp.json()["spend"][0]["cost_usd"] == pytest.approx(0.2)


# --- reconciliation: the dataset's totals must match UsageTotal.of and GET /api/spend -


def test_spend_reconciles_with_usage_total_and_the_fleet_spend_route(tmp_path: Path) -> None:
    hub, token, _graph_id, nodes = _seeded_hub(tmp_path)
    chunk_a = _mint_chunk(hub, token, ref="1")
    chunk_b = _mint_chunk(hub, token, ref="2")
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["build"], epoch=1, seq=1, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_a, node_id=nodes["review"], epoch=1, seq=2, cost_usd=None)
    _push_usage(hub, chunk_id=chunk_b, node_id=nodes["build"], epoch=1, seq=3, cost_usd=0.3)

    by_node = hub.client.get("/api/analytics/spend/nodes", headers=_cookie(token)).json()["spend"]
    since = (hub.clock.now() - timedelta(days=365)).isoformat()
    fleet_spend = hub.client.get("/api/spend", params={"since": since}, headers=_cookie(token)).json()

    summed_input = sum(row["input_tokens"] for row in by_node)
    summed_output = sum(row["output_tokens"] for row in by_node)
    summed_cache_read = sum(row["cache_read_tokens"] for row in by_node)
    summed_cache_create = sum(row["cache_create_tokens"] for row in by_node)
    summed_cost = sum(row["cost_usd"] for row in by_node)
    summed_partial = any(row["cost_partial"] for row in by_node)

    domain_total = UsageTotal.of(
        [
            UsageFact(
                node_id="n",
                epoch=1,
                kind="spawn",
                model="m",
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=10,
                cache_create_tokens=5,
                cost_usd=cost,
                recorded_at=hub.clock.now(),
            )
            for cost in (0.1, None, 0.3)
        ]
    )

    assert summed_input == domain_total.input_tokens == fleet_spend["input_tokens"]
    assert summed_output == domain_total.output_tokens == fleet_spend["output_tokens"]
    assert summed_cache_read == domain_total.cache_read_tokens == fleet_spend["cache_read_tokens"]
    assert summed_cache_create == domain_total.cache_create_tokens == fleet_spend["cache_create_tokens"]
    assert summed_cost == pytest.approx(domain_total.cost_usd) == pytest.approx(fleet_spend["cost_usd"])
    assert summed_partial == domain_total.cost_partial == fleet_spend["cost_partial"] is True
