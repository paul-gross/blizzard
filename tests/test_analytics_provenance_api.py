"""The operational analytics routes' provenance filters (component tier): ``model``,
``harness_id`` and ``harness_version`` narrow spend, durations and outcomes over a real
store by ``OperationalCriteria``'s attribution rule, and ``/spend/models`` rolls usage up
by model. Seeding follows ``tests/test_analytics_events_api.py``'s provenance pattern."""

from __future__ import annotations

import itertools
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from tests.support import HubHarness, report_lease
from tests.test_analytics_durations_api import complete_node, cookie, mint_chunk, seeded_hub

pytestmark = pytest.mark.component

_SEQ = itertools.count(1)  # one monotone seq per runner, shared by lease and usage facts


def _lease(hub: HubHarness, chunk_id: str, *, epoch: int) -> None:
    report_lease(hub, chunk_id, epoch=epoch, seq=next(_SEQ))


def _usage(
    hub: HubHarness,
    *,
    chunk_id: str,
    node_id: str,
    epoch: int,
    model: str,
    harness_id: str | None = None,
    harness_version: str | None = None,
    kind: str = "spawn",
    input_tokens: int = 100,
) -> None:
    payload = {
        "chunk_id": chunk_id,
        "node_id": node_id,
        "epoch": epoch,
        "kind": kind,
        "model": model,
        "harness_id": harness_id,
        "harness_version": harness_version,
        "input_tokens": input_tokens,
        "output_tokens": 50,
        "cache_read_tokens": 0,
        "cache_create_tokens": 0,
        "cost_usd": 0.5,
    }
    resp = hub.client.post(
        "/api/fleet/events",
        json={"runner_id": "r1", "facts": [{"seq": next(_SEQ), "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text


def _seed(tmp_path: Path):
    """Chunk A: one 30s build step on ``m-a`` under harness ``claude_code``. Chunk B: a 10s
    epoch that ran on both models (a judged fail), then a 20s epoch on ``m-b``. Chunk C: a 5s
    step with no usage fact at all. Chunk D: an epoch on ``m-a`` that never completed."""
    hub, token, _graph_id, nodes = seeded_hub(tmp_path)
    build = nodes["build"]

    a = mint_chunk(hub, token, ref="1")
    _lease(hub, a, epoch=1)
    _usage(hub, chunk_id=a, node_id=build, epoch=1, model="m-a", harness_id="claude_code", harness_version="1.0")
    hub.clock.advance(timedelta(seconds=30))
    complete_node(hub, a, epoch=1, from_node_id=build, choice="pass")

    b = mint_chunk(hub, token, ref="2")
    _lease(hub, b, epoch=1)
    _usage(hub, chunk_id=b, node_id=build, epoch=1, model="m-a")
    _usage(hub, chunk_id=b, node_id=build, epoch=1, model="m-b", kind="judge")
    hub.clock.advance(timedelta(seconds=10))
    complete_node(hub, b, epoch=1, from_node_id=build, choice="fail")
    _lease(hub, b, epoch=2)
    _usage(hub, chunk_id=b, node_id=build, epoch=2, model="m-b")
    hub.clock.advance(timedelta(seconds=20))
    complete_node(hub, b, epoch=2, from_node_id=build, choice="pass")

    c = mint_chunk(hub, token, ref="3")
    _lease(hub, c, epoch=1)
    hub.clock.advance(timedelta(seconds=5))
    complete_node(hub, c, epoch=1, from_node_id=build, choice="pass")

    d = mint_chunk(hub, token, ref="4")
    _lease(hub, d, epoch=1)
    _usage(hub, chunk_id=d, node_id=build, epoch=1, model="m-a")
    _lease(hub, d, epoch=2)  # proves epoch 1 is over, unfinished
    return hub, token, nodes, {"a": a, "b": b, "c": c, "d": d}


def _get(hub: HubHarness, token: str, path: str, **params: str) -> dict[str, Any]:
    resp = hub.client.get(f"/api/analytics/{path}", params=params, headers=cookie(token))
    assert resp.status_code == 200, resp.text
    return resp.json()


def _spend_by_key(body: dict[str, Any]) -> dict[str, int]:
    return {r["key"]: r["input_tokens"] for r in body["spend"]}


def test_spend_models_rolls_usage_up_by_model_key_ascending(tmp_path: Path) -> None:
    hub, token, _nodes, _chunks = _seed(tmp_path)
    body = _get(hub, token, "spend/models")
    assert [r["key"] for r in body["spend"]] == ["m-a", "m-b"]
    assert _spend_by_key(body) == {"m-a": 300, "m-b": 200}
    assert all(r["graph_name"] is None and r["node_name"] is None for r in body["spend"])


def test_spend_models_totals_equal_the_unfiltered_node_totals(tmp_path: Path) -> None:
    hub, token, _nodes, _chunks = _seed(tmp_path)
    by_model = sum(_spend_by_key(_get(hub, token, "spend/models")).values())
    by_node = sum(_spend_by_key(_get(hub, token, "spend/nodes")).values())
    assert by_model == by_node == 500


@pytest.mark.parametrize("path", ["spend/models", "spend/nodes", "spend/graphs"])
def test_spend_filters_sum_only_the_matching_usage_facts(tmp_path: Path, path: str) -> None:
    hub, token, _nodes, _chunks = _seed(tmp_path)
    assert sum(_spend_by_key(_get(hub, token, path, model="m-a")).values()) == 300
    assert sum(_spend_by_key(_get(hub, token, path, model="m-b")).values()) == 200
    assert sum(_spend_by_key(_get(hub, token, path, harness_id="claude_code")).values()) == 100
    assert sum(_spend_by_key(_get(hub, token, path, harness_version="1.0")).values()) == 100
    assert sum(_spend_by_key(_get(hub, token, path, model="m-b", harness_id="claude_code")).values()) == 0


def test_a_null_harness_matches_no_harness_filter(tmp_path: Path) -> None:
    hub, token, _nodes, _chunks = _seed(tmp_path)
    assert _get(hub, token, "spend/models", harness_id="opencode")["spend"] == []
    assert _get(hub, token, "spend/models", harness_version="2.0")["spend"] == []


def test_spend_chunks_and_its_ndjson_stream_honor_the_provenance_filters(tmp_path: Path) -> None:
    hub, token, _nodes, chunks = _seed(tmp_path)
    page = _get(hub, token, "spend/chunks", model="m-b")
    assert [r["chunk_id"] for r in page["spend"]] == [chunks["b"]]
    assert page["spend"][0]["input_tokens"] == 200
    resp = hub.client.get(
        "/api/analytics/spend/chunks/ndjson", params={"harness_id": "claude_code"}, headers=cookie(token)
    )
    assert resp.status_code == 200, resp.text
    assert [line.split('"')[3] for line in resp.text.splitlines()] == [chunks["a"]]


def _durations(body: dict[str, Any]) -> tuple[int, float]:
    rows = body["durations"]
    return sum(r["completed_steps"] for r in rows), sum(r["total_seconds"] for r in rows)


@pytest.mark.parametrize("path", ["durations/nodes", "durations/graphs"])
def test_durations_count_only_attempts_with_a_matching_usage_fact(tmp_path: Path, path: str) -> None:
    hub, token, _nodes, _chunks = _seed(tmp_path)
    assert _durations(_get(hub, token, path)) == (4, 65.0)
    assert _durations(_get(hub, token, path, model="m-a")) == (2, 40.0)  # A, and B's mixed first epoch
    assert _durations(_get(hub, token, path, model="m-b")) == (2, 30.0)  # the mixed epoch counts under each
    assert _durations(_get(hub, token, path, harness_id="claude_code")) == (1, 30.0)
    assert _get(hub, token, path, harness_id="opencode")["durations"] == []


def test_a_step_with_no_usage_fact_matches_no_provenance_filter(tmp_path: Path) -> None:
    hub, token, _nodes, _chunks = _seed(tmp_path)
    steps, seconds = _durations(_get(hub, token, "durations/nodes", model="m-a"))
    assert (steps, seconds) == (2, 40.0)  # chunk C's 5s step is in neither model's total


def test_outcomes_count_only_attempts_with_a_matching_usage_fact(tmp_path: Path) -> None:
    hub, token, nodes, _chunks = _seed(tmp_path)
    build = nodes["build"]

    def outcome(**params: str) -> dict[str, Any]:
        rows = _get(hub, token, "outcomes/nodes", **params)["outcomes"]
        absent: dict[str, Any] = {"choice_counts": {}, "attempt_failures": 0}
        return next((r for r in rows if r["node_id"] == build), absent)

    assert outcome() == {"node_id": build, "choice_counts": {"pass": 3, "fail": 1}, "attempt_failures": 1}
    assert outcome(model="m-a") == {"node_id": build, "choice_counts": {"pass": 1, "fail": 1}, "attempt_failures": 1}
    assert outcome(model="m-b") == {"node_id": build, "choice_counts": {"pass": 1, "fail": 1}, "attempt_failures": 0}
    assert outcome(harness_id="opencode") == {"choice_counts": {}, "attempt_failures": 0}


def test_a_failed_attempts_node_is_still_inferred_from_a_non_matching_epochs_movement(tmp_path: Path) -> None:
    """Epoch 1 (no usage) moved the chunk to ``review``; epoch 2 ran on ``m-e`` and died. The filter
    narrows only the candidate attempts, so epoch 2's failure still resolves to ``review``."""
    hub, token, _graph_id, nodes = seeded_hub(tmp_path)
    chunk_id = mint_chunk(hub, token)
    _lease(hub, chunk_id, epoch=1)
    complete_node(hub, chunk_id, epoch=1, from_node_id=nodes["build"], choice="pass")
    _lease(hub, chunk_id, epoch=2)
    _usage(hub, chunk_id=chunk_id, node_id=nodes["review"], epoch=2, model="m-e")
    _lease(hub, chunk_id, epoch=3)

    rows = _get(hub, token, "outcomes/nodes", model="m-e")["outcomes"]

    assert rows == [{"node_id": nodes["review"], "choice_counts": {}, "attempt_failures": 1}]
