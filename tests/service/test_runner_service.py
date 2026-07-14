"""Runner service tier — the real runner against the mock hub (verification/blizzard.md).

The **runner** daemon's reconciliation loop is exercised from outside, driven one real
``run_single_tick`` at a time (the steppable driver, ``bzh:steppable-loop``) against a
**mock hub** run as its own subprocess — the counterpart mocked (``implementation/
mocking.md``, "the runner → run it against the mock hub"). The mock hub's levers
manufacture the rare states a real hub could only be contrived into, so the tick's
resilience logic is asserted directly:

* **unreachable hub → buffered completion** — the completion is store-and-forward durable
  (D-069): while the hub is down the chunk never advances and the runner's outbound buffer
  holds the fact; when the hub heals the buffered completion flushes and the chunk lands.
* **dropped ack → idempotent re-apply** — the hub applies the transition but drops the ack
  (503); the runner re-flushes the same completion and the hub's epoch-idempotent apply
  (D-090) advances the chunk exactly once — no double transition — through to done.
* **stale envelope tolerated** — the hub serves a stale-epoch envelope; the runner fences
  its completion on its own lease epoch (not the envelope's), so the chunk still lands.

Every seam is real (fixture workspace, mock-claude-code, git), no tokens, no network.
Reproduce — from a provisioned feature env — with::

    BLIZZARD_SERVICE=1 uv run pytest tests/service/test_runner_service.py
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
from tests.e2e.test_acceptance_loop import REPO, _free_port, _git_bare, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_PM_URL = f"{REPO}/issues/1"


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.5) -> None:
    """Run ``ticks`` synchronous reconciliation passes with the harness fence set."""
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            run_single_tick(config)
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _status(hub: httpx.Client, chunk_id: str) -> str:
    return hub.get(f"/api/chunks/{chunk_id}").json()["status"]


def _pending_outbound(config: RunnerConfig) -> int:
    """The depth of the runner's store-and-forward buffer (D-069)."""
    engine = create_engine_from_url(config.db_url)
    try:
        return len(SqlAlchemyRunnerStore(engine).pending_outbound())
    finally:
        engine.dispose()


def _seed(hub: httpx.Client) -> str:
    resp = hub.post("/_seed/chunk", json=mock_hub_chunk_spec(_PM_URL))
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def test_unreachable_hub_buffers_the_completion_then_lands_on_recovery(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, origin_bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drive (hub up) until the mock worker has committed, exited, and ADVANCE has
        # *buffered* the completion — the tick boundary just before PULL would flush it
        # (ADVANCE enqueues; the flush is the next tick's PULL, so we can wedge in here).
        buffered = poll_until(lambda: _tick_then(config, fenced, lambda: _pending_outbound(config) >= 1), timeout=60.0)
        assert buffered, "the completion never buffered (the worker did not run to completion)"
        assert _status(hub, chunk_id) != "done", "the chunk landed before the outage could be staged"

        # Now the hub goes unreachable: every flush attempt fails, so the completion stays
        # store-and-forward buffered (D-069). The chunk's status is unreadable *because* the
        # hub is down — which is the point — so the buffer depth is the proof it did not flush.
        assert hub.post("/_levers/unreachable", json={"remaining": 10_000}).status_code == 200
        _drive(config, fenced, ticks=4)
        assert _pending_outbound(config) >= 1, "the completion did not stay buffered during the outage (D-069)"

        # Heal the hub; the buffered completion flushes and the chunk lands.
        assert hub.post("/_levers/reset").status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=60.0)
        assert landed, f"chunk did not land after recovery (status {_status(hub, chunk_id)!r})"
        assert _pending_outbound(config) == 0, "the outbound buffer did not drain after recovery"

    # The runner pushed the mock harness's commit to the bare origin (the artifact-push half
    # of ADVANCE, D-026) — on the work branch. Unlike e2e, the mock hub fakes the deliver
    # node, so the commit is not merged to main; it is reachable across the origin's refs.
    reachable = _git_bare(origin_bare, "log", "--all", "--name-only", "--format=")
    assert "LANDED.md" in reachable.split(), "the mock harness's commit never reached the bare origin"


def test_dropped_ack_reapplies_idempotently_through_to_done(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Drop the very first completion ack: the hub advances build -> deliver but answers
        # 503, so the runner keeps the completion buffered and re-flushes it. The hub's
        # epoch-idempotent apply (D-090) must advance the chunk exactly once.
        assert hub.post("/_levers/drop_ack", json={"chunk_id": chunk_id, "remaining": 1}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land after the dropped ack (status {_status(hub, chunk_id)!r})"
        # done is reached once — a double apply would have errored or re-run the deliver node.
        assert _status(hub, chunk_id) == "done"


def test_stale_envelope_is_tolerated_and_the_chunk_still_lands(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        # Serve a stale-epoch envelope on the re-read: the runner fences its completion on
        # its own lease epoch, not the envelope's, so a stale envelope is benign — the chunk
        # still lands. (The lever is single-shot; it perturbs one envelope read.)
        assert hub.post("/_levers/stale_envelope", json={"chunk_id": chunk_id, "remaining": 1}).status_code == 200
        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land despite a stale envelope (status {_status(hub, chunk_id)!r})"


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    """One tick + a status read — the poll predicate the scenarios share."""
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target


def _tick_then(config: RunnerConfig, fenced: dict[str, str], check) -> bool:
    """One tick, then evaluate ``check`` — the buffered-completion poll predicate."""
    _drive(config, fenced, ticks=1, pause=0.3)
    return bool(check())
