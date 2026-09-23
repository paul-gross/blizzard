"""``GET /api/fleet/chunks/{chunk_id}/analytics/...`` — a worker's own routine-run read
of the six operator counts/spend summaries over a window it names (blizzard#545).
Component tier: each route renders the identical rows the matching operator route
renders for the same window, reusing its own query criteria and response-shaping
helpers rather than a second aggregation; ``since`` is required (422 unset); a chunk
with no run context, and an unknown chunk, both 404; and the operator counts/spend
routes still refuse a runner principal (the existing sweep in ``test_analytics_events_api.py``
/ ``test_analytics_spend_api.py`` stands unchanged)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.hub.domain.run_context import RunContext
from blizzard.hub.domain.work import WorkItemAuthor
from blizzard.hub.store.internal.run_context_store import RunContextStore
from blizzard.hub.store.internal.work_item_store import WorkItemStore
from tests.support import HubHarness, build_hub, hub_store_connections, seed_graph, seed_work_item

pytestmark = pytest.mark.component

_NOW = datetime(2026, 9, 2, 12, 0, 0, tzinfo=UTC)
_ROUTINE = "nightly"
_SCOPE = "blizzard"
_GRAPH_ID = "gr_analytics"

#: Every fleet analytics route this module holds to the same-rows/404/422 sweeps.
_COUNTS_ROUTES = [
    "counts/files",
    "counts/skills",
    "counts/agent-types",
    "counts/nodes",
]
_SPEND_ROUTES = ["spend/nodes", "spend/graphs"]
_ROUTES = _COUNTS_ROUTES + _SPEND_ROUTES

#: The matching operator route for each fleet suffix — "same rows" is proven against these.
_OPERATOR_PATH = {
    "counts/files": "/api/analytics/counts/files",
    "counts/skills": "/api/analytics/counts/skills",
    "counts/agent-types": "/api/analytics/counts/agent-types",
    "counts/nodes": "/api/analytics/counts/nodes",
    "spend/nodes": "/api/analytics/spend/nodes",
    "spend/graphs": "/api/analytics/spend/graphs",
}


def _seed_chunk(hub: HubHarness, *, with_run_context: bool = True) -> str:
    """A work item with its own resting chunk, plus a recorded run context for it
    unless ``with_run_context`` is False — the chunk id the route resolves through."""
    store_connections = hub_store_connections(hub.engine)
    with hub.engine.begin() as conn:
        seed_graph(conn, _GRAPH_ID, at=_NOW)
    items = WorkItemStore(store_connections)
    item = seed_work_item(items, graph_id=_GRAPH_ID, author=WorkItemAuthor.user("u_1"), at=_NOW)
    if with_run_context:
        RunContextStore(store_connections).record(
            item.work_item_id, RunContext(routine_name=_ROUTINE, scope_slug=_SCOPE, mode="dry_run")
        )
    return f"ch_{item.ref}"


def _push_usage(hub: HubHarness, *, chunk_id: str, node_id: str, epoch: int, seq: int, cost_usd: float) -> None:
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


def _tool_turn(index: int, name: str, input: dict[str, object], *, timestamp: str) -> dict:
    return {
        "index": index,
        "kind": "tool",
        "timestamp": timestamp,
        "text": "",
        "tool": {
            "name": name,
            "input": input,
            "input_unparsed": None,
            "input_shape": "object",
            "tool_use_id": f"t{index}",
            "output": None,
            "output_truncated": False,
        },
        "thinking_redacted": False,
        "sidechain": None,
        "truncated": False,
    }


def _push_transcript(hub: HubHarness, *, chunk_id: str, node_id: str) -> None:
    spawn = _tool_turn(2, "Agent", {"subagent_type": "explorer"}, timestamp="2026-08-12T11:00:00Z")
    # A sidechain under the spawn (blizzard#545's own agent-types row): a main-lane
    # spawn's own `agent_type` column is unset (test_analytics_events_api.py's own
    # `test_counts_by_agent_type_is_empty_at_the_main_lane`); the count groups on the
    # enclosing sidechain's column instead.
    spawn["sidechain"] = {
        "agent_id": "ag_1",
        "agent_type": "explorer",
        "link": "resolved",
        "turns": [_tool_turn(0, "Read", {"file_path": "src/c.py"}, timestamp="2026-08-12T11:01:00Z")],
    }
    turns = [
        _tool_turn(0, "Read", {"file_path": "src/a.py"}, timestamp="2026-08-12T09:00:00Z"),
        _tool_turn(1, "Skill", {"skill": "wf-commit"}, timestamp="2026-08-12T10:00:00Z"),
        spawn,
    ]
    record = {
        "seq": 1,
        "segment_id": "sg_1",
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": 1,
        "spawn_generation": 1,
        "turn_range_start": 0,
        "turn_range_end": len(turns) - 1,
        "final": True,
        "normalizer_version": "claude-code-jsonl/2",
        "harness_version": "claude-code-1.0",
        "harness_id": None,
        "model": None,
        "effort": None,
        "turns": turns,
    }
    resp = hub.client.post("/api/fleet/transcripts", json={"runner_id": "r1", "records": [record]})
    assert resp.status_code == 200, resp.text
    hub.services.event_derivation.sweep()


def _seeded_hub(tmp_path: Path) -> tuple[HubHarness, str]:
    """A routine-run chunk carrying a file_read/skill_invocation/agent_spawn triad and
    two usage facts on the same node — enough live data for every counts/spend route to
    return a non-empty row."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    node_id = "nd_build"
    _push_transcript(hub, chunk_id=chunk_id, node_id=node_id)
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.1)
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=3, cost_usd=0.2)
    return hub, chunk_id


def _fleet_path(chunk_id: str, suffix: str) -> str:
    return f"/api/fleet/chunks/{chunk_id}/analytics/{suffix}"


# --- 404s: unknown chunk, no run context -------------------------------------------


@pytest.mark.parametrize("suffix", _ROUTES)
def test_404s_on_an_unknown_chunk(tmp_path: Path, suffix: str) -> None:
    hub = build_hub(tmp_path)
    resp = hub.client.get(_fleet_path("ch_ghost", suffix), params={"since": "2026-01-01T00:00:00Z"})
    assert resp.status_code == 404, resp.text
    assert "unknown chunk" in resp.json()["detail"]


@pytest.mark.parametrize("suffix", _ROUTES)
def test_404s_on_a_chunk_with_no_run_context(tmp_path: Path, suffix: str) -> None:
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub, with_run_context=False)
    resp = hub.client.get(_fleet_path(chunk_id, suffix), params={"since": "2026-01-01T00:00:00Z"})
    assert resp.status_code == 404, resp.text
    assert "no run context" in resp.json()["detail"]


# --- required window: since 422s unset, until alone is accepted --------------------


@pytest.mark.parametrize("suffix", _ROUTES)
def test_422s_without_since(tmp_path: Path, suffix: str) -> None:
    hub, chunk_id = _seeded_hub(tmp_path)
    resp = hub.client.get(_fleet_path(chunk_id, suffix))
    assert resp.status_code == 422, resp.text


@pytest.mark.parametrize("suffix", _ROUTES)
def test_a_since_until_pair_is_accepted(tmp_path: Path, suffix: str) -> None:
    """`until` is optional alongside the required `since` — supplying both 200s. This
    proves nothing about `until` narrowing the query; that's the pair of tests below."""
    hub, chunk_id = _seeded_hub(tmp_path)
    resp = hub.client.get(
        _fleet_path(chunk_id, suffix), params={"since": "2020-01-01T00:00:00Z", "until": "2030-01-01T00:00:00Z"}
    )
    assert resp.status_code == 200, resp.text


# --- same rows as the matching operator route ---------------------------------------


@pytest.mark.parametrize("suffix", _ROUTES)
def test_same_rows_as_the_operator_route(tmp_path: Path, suffix: str) -> None:
    hub, chunk_id = _seeded_hub(tmp_path)
    params = {"since": "2020-01-01T00:00:00Z", "until": "2030-01-01T00:00:00Z"}

    fleet = hub.client.get(_fleet_path(chunk_id, suffix), params=params)
    operator = hub.client.get(_OPERATOR_PATH[suffix], params=params)

    assert fleet.status_code == 200, fleet.text
    assert operator.status_code == 200, operator.text
    assert fleet.json() == operator.json()
    key = "counts" if suffix.startswith("counts/") else "spend"
    assert fleet.json()[key] != []  # a window covering the seed proves the window reaches the query


@pytest.mark.parametrize("suffix", _ROUTES)
def test_a_window_excluding_the_seed_returns_no_rows(tmp_path: Path, suffix: str) -> None:
    """The window demonstrably reaches the query, not just the run-context gate."""
    hub, chunk_id = _seeded_hub(tmp_path)
    params = {"since": "2030-01-01T00:00:00Z"}

    fleet = hub.client.get(_fleet_path(chunk_id, suffix), params=params)
    operator = hub.client.get(_OPERATOR_PATH[suffix], params=params)

    assert fleet.status_code == 200, fleet.text
    key = "counts" if suffix.startswith("counts/") else "spend"
    assert fleet.json()[key] == []
    assert fleet.json() == operator.json()


@pytest.mark.parametrize("suffix", _COUNTS_ROUTES)
def test_until_narrows_the_window(tmp_path: Path, suffix: str) -> None:
    """`until` must reach the query, not just gate a 200 (blizzard#545 review F4): a
    window ending before the seed's later turns excludes the rows they alone produce,
    so deleting `until` from `AnalyticsWindow.scope` would turn this case red."""
    hub, chunk_id = _seeded_hub(tmp_path)
    since = {"since": "2020-01-01T00:00:00Z"}

    full = hub.client.get(_fleet_path(chunk_id, suffix), params=since)
    narrowed = hub.client.get(_fleet_path(chunk_id, suffix), params={**since, "until": "2026-08-12T09:30:00Z"})

    assert full.status_code == 200, full.text
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json() != full.json()


@pytest.mark.parametrize("suffix", _SPEND_ROUTES)
def test_until_narrows_the_window_for_spend(tmp_path: Path, suffix: str) -> None:
    """`until` narrows the spend rollups too (blizzard#545 review F4): a window ending
    before the second usage fact's `recorded_at` excludes the cost it alone adds."""
    hub = build_hub(tmp_path)
    chunk_id = _seed_chunk(hub)
    node_id = "nd_build"
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=1, cost_usd=0.1)
    hub.clock.advance(timedelta(days=1))
    cutoff = hub.clock.now().isoformat()
    _push_usage(hub, chunk_id=chunk_id, node_id=node_id, epoch=1, seq=2, cost_usd=0.2)
    since = {"since": "2020-01-01T00:00:00Z"}

    full = hub.client.get(_fleet_path(chunk_id, suffix), params=since)
    narrowed = hub.client.get(_fleet_path(chunk_id, suffix), params={**since, "until": cutoff})

    assert full.status_code == 200, full.text
    assert narrowed.status_code == 200, narrowed.text
    assert narrowed.json() != full.json()


# --- no extra filter reaches the route ----------------------------------------------


@pytest.mark.parametrize("suffix", _ROUTES)
def test_takes_no_graph_or_source_filter(tmp_path: Path, suffix: str) -> None:
    """The route's own signature carries no such parameter — passing one is a no-op."""
    hub, chunk_id = _seeded_hub(tmp_path)
    params = {"since": "2020-01-01T00:00:00Z", "graph_id": "gr_other", "source": "other"}

    resp = hub.client.get(_fleet_path(chunk_id, suffix), params=params)

    assert resp.status_code == 200, resp.text
    key = "counts" if suffix.startswith("counts/") else "spend"
    assert resp.json()[key] != []
