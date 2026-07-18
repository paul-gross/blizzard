"""Usage service tier — usage facts over the wire, both daemon directions (epic #57, #59).

Phase 3 makes usage a fleet-visible fact that flows runner -> hub on the store-and-forward
rails and becomes a **derived** chunk total the hub serves off its live API. The unit and
component tiers pin the derivation and the in-process ingest; this file proves the same two
behaviours against a **running** daemon over real HTTP with the counterpart mocked — the
tier that alone type-checks a wire field name off a live response (``bzh:sweep-release-only-
tiers``), which the new ``ChunkUsageView`` / ``ChunkUsageTotalView`` shapes are:

* **runner -> mock hub (store-and-forward)** — a real runner drives a build against the mock
  hub; ADVANCE records the worker's usage and buffers each fact outbound on the same rails
  ``lease.minted`` / ``completion.submitted`` ride. Through a hub outage the usage facts stay
  buffered (they cannot flush), and on recovery they flush and the buffer drains to zero —
  recorded once, never re-buffered by the subsequent ticks (the seq high-water idempotency).
* **mock runner -> live hub (derived totals)** — the mock runner claims a chunk over the wire
  (a real lease + epoch minted on the running hub); usage facts are then pushed through the
  hub's real ``POST /api/events`` store-and-forward endpoint, and the derived per-node-step
  usage + chunk total (with ``cost_partial`` when a row's cost is absent) are read back off
  the **live** ``GET /api/chunks/{id}`` and ``GET /api/chunks`` responses. A replayed seq
  lands nothing twice.

sqlite only, no tokens, no network. Reproduce — from a provisioned feature env — with::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_usage_service.py
"""

from __future__ import annotations

import os
import time
from pathlib import Path

import httpx
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import run_single_tick
from blizzard.runner.store.internal.sqlalchemy_store import SqlAlchemyRunnerStore
from blizzard.wire.facts import USAGE_RECORDED
from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    mock_runner,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_PM_URL = f"{REPO}/issues/1"


# --------------------------------------------------------------------------- #
# runner -> mock hub: usage rides the store-and-forward buffer through an outage.
# --------------------------------------------------------------------------- #


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.3) -> None:
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            run_single_tick(config)
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _pending_usage(config: RunnerConfig) -> int:
    """The count of buffered, not-yet-flushed ``usage.recorded`` outbound facts."""
    engine = create_engine_from_url(config.db_url)
    try:
        return len([b for b in SqlAlchemyRunnerStore(engine).pending_outbound() if b.kind == USAGE_RECORDED])
    finally:
        engine.dispose()


def _pending_total(config: RunnerConfig) -> int:
    engine = create_engine_from_url(config.db_url)
    try:
        return len(SqlAlchemyRunnerStore(engine).pending_outbound())
    finally:
        engine.dispose()


def _status(hub: httpx.Client, chunk_id: str) -> str:
    return hub.get(f"/api/chunks/{chunk_id}").json()["status"]


def _tick_then_usage_buffered(config: RunnerConfig, fenced: dict[str, str]) -> bool:
    _drive(config, fenced, ticks=1)
    return _pending_usage(config) >= 1


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    _drive(config, fenced, ticks=1)
    return _status(hub, chunk_id) == target


def test_runner_buffers_usage_facts_through_a_hub_outage_and_flushes_once(tmp_path: Path) -> None:
    """A real runner's usage facts ride the store-and-forward buffer: buffered while the
    build runs, held durable through a hub outage, flushed on recovery, recorded once."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_PM_URL))
        assert resp.status_code == 201, resp.text
        chunk_id = resp.json()["chunk_id"]
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drive (hub up) until the mock worker has committed, exited, and ADVANCE has
        # *buffered* its usage fact(s) — caught on the tick boundary before the next
        # PULL would flush them (ADVANCE enqueues on the same rails as the completion).
        buffered = poll_until(lambda: _tick_then_usage_buffered(config, fenced), timeout=60.0)
        assert buffered, "no usage.recorded fact ever buffered (the worker did not run to completion)"
        assert _status(hub, chunk_id) != "done", "the chunk landed before the outage could be staged"

        # The hub goes unreachable: every flush attempt fails, so the usage facts stay
        # store-and-forward buffered — they do not vanish and are not double-counted.
        assert hub.post("/_levers/unreachable", json={"remaining": 10_000}).status_code == 200
        _drive(config, fenced, ticks=4)
        assert _pending_usage(config) >= 1, "the usage fact did not stay buffered during the outage"

        # Heal the hub; the buffered usage (and completion) flush and the chunk lands.
        assert hub.post("/_levers/reset").status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=60.0)
        assert landed, f"chunk did not land after recovery (status {_status(hub, chunk_id)!r})"
        assert _pending_total(config) == 0, "the outbound buffer did not drain after recovery"

        # Recorded once: the extra ticks after the drain re-buffer no usage fact.
        _drive(config, fenced, ticks=2)
        assert _pending_usage(config) == 0, "usage was re-buffered after the flush — not recorded exactly once"


# --------------------------------------------------------------------------- #
# mock runner -> live hub: derived chunk usage totals off the running HTTP API.
# --------------------------------------------------------------------------- #


def _graph_yaml() -> str:
    import yaml

    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": "# build",
                "judgement": {"prompt": "# judge", "choices": {"pass": {"description": "green", "to": "deliver"}}},
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "run": [{"command": "true"}],
                "judgement": {
                    "choices": {
                        "landed": {"description": "Every repo merged cleanly.", "to": "done"},
                        "conflict": {"description": "A repo did not merge cleanly.", "to": "build"},
                    },
                },
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _ingest(forge: httpx.Client, hub: httpx.Client, title: str) -> str:
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id


def _usage_payload(chunk_id: str, node_id: str, *, epoch: int, cost_usd: float | None) -> dict:
    return {
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


def _push_usage(hub: httpx.Client, *, runner_id: str, seq: int, payload: dict) -> dict:
    resp = hub.post(
        "/api/events",
        json={"runner_id": runner_id, "facts": [{"seq": seq, "kind": "usage.recorded", "payload": payload}]},
    )
    assert resp.status_code == 200, resp.text
    return resp.json()


def test_hub_derives_chunk_usage_totals_off_a_live_api_from_pushed_facts(tmp_path: Path) -> None:
    """The mock runner claims a chunk over the wire (a real lease + epoch on the running
    hub); usage facts pushed through the hub's real store-and-forward endpoint become
    per-node-step usage + a derived chunk total read back off the **live** API, with
    ``cost_partial`` set when a row's cost is absent, and a replay lands nothing twice."""
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()

    with _forge(bin_dir, origins, forge_port) as forge, _hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": _graph_yaml()}).status_code == 201
        chunk_id = _ingest(forge, hub, "usage totals over the wire")

        with mock_runner(bin_dir, _free_port(), hub_port, runner_id="runner-usage") as runner:
            assert runner.post("/_drive/register").json()["status"] == 201
            claim = runner.post("/_drive/claim", json={"chunk_id": chunk_id}).json()
            assert claim["claimed"] is True, claim
            node_id = claim["from_node_id"]

        # The claim minted a real lease + epoch on the running hub — read it back over the wire.
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        epoch = detail["latest_epoch"] or 1

        # Push two usage facts on the runner's store-and-forward endpoint: one carrying a
        # cost, one with cost absent (the envelope-less transcript-summation fallback).
        assert _push_usage(
            hub, runner_id="usage-pusher", seq=1, payload=_usage_payload(chunk_id, node_id, epoch=epoch, cost_usd=0.10)
        )["applied"] == [1]
        assert _push_usage(
            hub, runner_id="usage-pusher", seq=2, payload=_usage_payload(chunk_id, node_id, epoch=epoch, cost_usd=None)
        )["applied"] == [2]

        # Per-node-step usage + the derived chunk total, read off the LIVE detail API.
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert len(detail["usage"]) == 2, detail["usage"]
        step = detail["usage"][0]
        assert step["node_id"] == node_id
        assert step["epoch"] == epoch
        assert step["input_tokens"] == 100
        assert step["cache_create_tokens"] == 5

        total = detail["cost"]
        assert total["input_tokens"] == 200  # both rows summed by class
        assert total["output_tokens"] == 100
        assert total["cost_usd"] == pytest.approx(0.10)  # only the cost-bearing row — the lower bound
        assert total["cost_partial"] is True  # a cost-absent row flags the total partial

        # The summary listing carries the derived cost total too.
        row = next(c for c in hub.get("/api/chunks").json() if c["chunk_id"] == chunk_id)
        assert row["cost"]["cost_usd"] == pytest.approx(0.10)
        assert row["cost"]["cost_partial"] is True

        # Idempotent replay: a re-pushed seq lands nothing twice — the total is unchanged.
        replay = _push_usage(
            hub, runner_id="usage-pusher", seq=2, payload=_usage_payload(chunk_id, node_id, epoch=epoch, cost_usd=None)
        )
        assert replay["applied"] == [] and replay["already_applied"] == [2], replay
        detail = hub.get(f"/api/chunks/{chunk_id}").json()
        assert len(detail["usage"]) == 2, "the replayed usage fact was applied twice"
        assert detail["cost"]["input_tokens"] == 200
