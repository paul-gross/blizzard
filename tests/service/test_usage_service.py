"""Usage service tier — usage facts over the wire, the runner direction (epic #57, #59).

Proves the store-and-forward flow against a running daemon over real HTTP
(``bzh:ci-job-local-command``): a real runner buffers usage through a mock-hub outage
and flushes once on recovery. The mock-runner-to-live-hub direction runs in
``tests/service/hub/usage.spec.ts``."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx2
import pytest

from blizzard.foundation.fact_kinds import USAGE_RECORDED
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import REPO, runner_config
from tests.fleet_world import mint_world
from tests.runner_fakes import daemon_store
from tests.runner_join import fleet_headers
from tests.service.support import (
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    service_gate,
)
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = f"{REPO}/issues/1"


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
            LoopWiring.of(config).tick_once()
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _pending_usage(config: RunnerConfig) -> int:
    """The count of buffered, not-yet-flushed ``usage.recorded`` outbound facts."""
    engine = create_engine_from_url(config.db_url)
    try:
        return len([b for b in daemon_store(engine).pending_outbound() if b.kind == USAGE_RECORDED])
    finally:
        engine.dispose()


def _pending_usage_payloads(config: RunnerConfig) -> list[dict]:
    """Every buffered ``usage.recorded`` fact's own JSON payload, oldest first."""
    engine = create_engine_from_url(config.db_url)
    try:
        return [json.loads(b.payload) for b in daemon_store(engine).pending_outbound() if b.kind == USAGE_RECORDED]
    finally:
        engine.dispose()


def _pending_total(config: RunnerConfig) -> int:
    engine = create_engine_from_url(config.db_url)
    try:
        return len(daemon_store(engine).pending_outbound())
    finally:
        engine.dispose()


def _status(hub: httpx2.Client, chunk_id: str) -> str:
    """The mock hub's own status read — distinct from the live-hub reads further down
    this file, which stay at the real hub's anonymous ``GET /api/chunks/{id}``."""
    return hub.get(f"/api/fleet/chunks/{chunk_id}", headers=fleet_headers(hub)).json()["status"]


def _tick_then_usage_buffered(config: RunnerConfig, fenced: dict[str, str]) -> bool:
    _drive(config, fenced, ticks=1)
    return _pending_usage(config) >= 1


def _run_and_check(
    config: RunnerConfig, fenced: dict[str, str], hub: httpx2.Client, chunk_id: str, target: str
) -> bool:
    _drive(config, fenced, ticks=1)
    return _status(hub, chunk_id) == target


def test_runner_buffers_usage_facts_through_a_hub_outage_and_flushes_once(tmp_path: Path) -> None:
    """A real runner's usage facts ride the store-and-forward buffer: buffered while the
    build runs, held durable through a hub outage, flushed on recovery, recorded once."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_WORK_REF_URL))
        assert resp.status_code == 201, resp.text
        chunk_id = resp.json()["chunk_id"]
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drive until the mock worker commits and ADVANCE buffers its usage fact(s),
        # caught before the next PULL would flush them.
        buffered = poll_until(lambda: _tick_then_usage_buffered(config, fenced), timeout=60.0)
        assert buffered, "no usage.recorded fact ever buffered (the worker did not run to completion)"
        assert _status(hub, chunk_id) != "done", "the chunk landed before the outage could be staged"

        # The buffered payload carries the lease's own harness stamp,
        # stamped from the real spawn's own recorded identity, never fabricated.
        payloads = _pending_usage_payloads(config)
        assert payloads and payloads[0]["harness_id"] == "claude_code", payloads

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
