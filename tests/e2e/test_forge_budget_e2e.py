"""The shared forge rate budget, end to end — the `test_forge_budget_e2e` scenario of the standing e2e smoke.

Drives the mock forge's rate levers against a live hub whose work source and repository share one forge key. Covers:
a remaining below two fifths of the limit turning the forge-status sweep away, with no request sent, while a landing
still proceeds; a close intent the budget turns away deferred to the reset without spending its backoff; a landing
the forge refuses for its rate — its budget spent, or a secondary limit — pending with its next poll at the refusal's
lift — for a spent budget, the window's reset — and landing after it; and ``forge-budget-low`` and ``forge-rate-limited`` each recorded once per window."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import httpx2
import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import REPO, git_bare, mock_bin_dir, run_forge, run_hub, runner_api, runner_config
from tests.fleet_world import FIXTURE_ENV, REPO_NAME, graph_yaml, mint_world
from tests.support import FIXTURE_FORGE_SECRET, free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e forge-budget needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: The mock forge's limit, GitHub's own; a remaining of 30% of it sits below sweeping's two fifths.
_LIMIT = 5000
_LOW_REMAINING = _LIMIT * 3 // 10
#: The mock answers either rate refusal with this ``Retry-After``.
_RETRY_AFTER = 60


def _poll_until(predicate: Callable[[], Any], *, timeout: float = 30.0, interval: float = 0.3) -> Any:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        value = predicate()
        if value:
            return value
        time.sleep(interval)
    raise AssertionError("the awaited state never arrived within the timeout")


def _world(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    scratch = tmp_path / "scratch"
    mint_world(bin_dir, scratch)
    fixture_root = scratch / FIXTURE_ENV
    return bin_dir, fixture_root / "workspace", fixture_root / "origins"


def _main_tree(origins: Path) -> list[str]:
    return git_bare(origins / f"{REPO_NAME}.git", "ls-tree", "-r", "--name-only", "main").split()


def _labels(forge: httpx2.Client, number: int) -> set[str]:
    resp = forge.get(f"/repos/{REPO}/issues/{number}")
    assert resp.status_code == 200, resp.text
    return {label["name"] for label in resp.json()["labels"]}


def _ingest(hub: httpx2.Client, forge: httpx2.Client, title: str, *, promote: bool = True) -> tuple[str, int]:
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": title, "body": "the chunk"})
    assert issue.status_code == 201, issue.text
    number = issue.json()["number"]
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    if promote:
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    return chunk_id, number


def _drive_until(
    config: RunnerConfig, hub: httpx2.Client, chunk_id: str, predicate: Callable[[dict[str, Any]], bool], timeout: float
) -> dict[str, Any]:
    """Tick the runner until the chunk's detail satisfies ``predicate``; return that detail."""
    prior = dict(os.environ)
    os.environ.update({**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"})
    try:
        with runner_api(config):
            deadline = time.monotonic() + timeout
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once()
                detail = hub.get(f"/api/chunks/{chunk_id}")
                assert detail.status_code == 200, detail.text
                body = detail.json()
                if predicate(body):
                    return body
                time.sleep(0.4)
            raise AssertionError(f"chunk {chunk_id} never reached the awaited state")
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _budget(hub: httpx2.Client) -> dict[str, Any] | None:
    """The fixture secret's ``core`` reading of the one forge, or ``None`` before the forge has been heard from."""
    forges = hub.get("/api/forges").json()["forges"]
    assert len(forges) == 1, forges
    detail = hub.get(f"/api/forges/{forges[0]['forge_id']}")
    assert detail.status_code == 200, detail.text
    readings = [
        b for b in detail.json()["budgets"] if b["secret_name"] == FIXTURE_FORGE_SECRET and b["bucket"] == "core"
    ]
    return readings[0] if readings else None


def _events(hub: httpx2.Client, kind: str) -> list[dict[str, Any]]:
    feed = hub.get("/api/events", params={"limit": 200})
    assert feed.status_code == 200, feed.text
    return [event for event in feed.json()["events"] if event.get("kind") == kind]


def _sweep(hub: httpx2.Client, name: str) -> None:
    """One pass of ``name`` on request, waiting out a timed pass that holds its lock."""

    def ran() -> bool:
        resp = hub.post(f"/api/sweeps/{name}/passes")
        assert resp.status_code in (200, 409), resp.text
        return resp.status_code == 200

    _poll_until(ran, timeout=15.0)


def _instant(raw: str) -> datetime:
    parsed = datetime.fromisoformat(raw.replace(" ", "T").replace("Z", "+00:00"))
    return parsed if parsed.tzinfo is not None else parsed.replace(tzinfo=UTC)


def _close_attempts(hub_dir: Path, number: int) -> list[tuple[str, str | None]]:
    """``(outcome, resume_at)`` of every drain attempt at the close intent of issue ``number``, oldest first."""
    with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
        return conn.execute(
            "SELECT a.outcome, a.resume_at FROM close_intent_attempts a JOIN close_intents i ON i.id = a.intent_id"
            " WHERE i.ref = ? ORDER BY a.id",
            (str(number),),
        ).fetchall()


def _closure_facts(hub_dir: Path, chunk_id: str) -> int:
    with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
        return conn.execute("SELECT COUNT(*) FROM work_item_closures WHERE chunk_id = ?", (chunk_id,)).fetchone()[0]


def _pending_intents(hub_dir: Path, chunk_id: str) -> int:
    with sqlite3.connect(hub_dir / "data" / "hub.db") as conn:
        return conn.execute(
            "SELECT COUNT(*) FROM close_intents WHERE chunk_id = ? AND retired_at IS NULL", (chunk_id,)
        ).fetchone()[0]


def test_a_low_budget_turns_the_sweep_away_while_a_landing_proceeds(tmp_path: Path) -> None:
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(hub_dir, forge_port, hub_port, annotate=True, annotation_interval_seconds=1) as hub,
    ):
        assert hub.post("/api/graphs", json={"definition_yaml": graph_yaml()}).status_code == 201

        # A healthy budget: the sweep labels the first chunk.
        _chunk_a, number_a = _ingest(hub, forge, "labelled while the budget is healthy", promote=False)
        _poll_until(lambda: _labels(forge, number_a) == {"blizzard:ingested"})

        # The window falls below two fifths; the sweep's next answer teaches the hub so.
        reset = int(time.time()) + 900
        armed = forge.post("/_levers/rate_budget", json={"remaining": _LOW_REMAINING, "reset": reset})
        assert armed.status_code == 200, armed.text
        _poll_until(lambda: (b := _budget(hub)) is not None and b["remaining"] <= _LOW_REMAINING)
        # The pass that taught it may still be spending what it started healthy; a pass on request waits it out, and
        # is itself turned away, so the reading after it is the one every later pass must leave alone.
        _sweep(hub, "forge_status")
        low = _budget(hub)
        assert low is not None and low["remaining"] <= _LOW_REMAINING
        assert low["limit"] == _LIMIT
        assert _instant(low["reset"]).timestamp() == reset

        # Turned away: a newly ingested chunk is never labelled, and no request spends the budget — past the full-pass
        # floor (5x the 1 s interval), with passes on request too.
        chunk_b, number_b = _ingest(hub, forge, "never labelled while the budget is low")
        time.sleep(7)
        _sweep(hub, "forge_status")
        _sweep(hub, "forge_status")
        assert _labels(forge, number_b) == set()
        assert _budget(hub) == low, "a turned-away sweep still spent the budget"

        # A landing still proceeds, and its answers keep teaching the budget.
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        done = _drive_until(config, hub, chunk_b, lambda b: b["status"] in {"done", "stopped", "needs_human"}, 120)
        assert done["status"] == "done", done["status"]
        assert "LANDED.md" in _main_tree(origins), "the landing did not reach the bare origin's main"
        landed = _budget(hub)
        assert landed is not None and landed["remaining"] < low["remaining"]

        # The landed chunk's close is turned away too: deferred to the reset, with no backoff spent and no closure
        # fact, and not tried again before the reset.
        _sweep(hub, "close_drain")
        attempts = _poll_until(lambda: _close_attempts(hub_dir, number_b))
        assert [outcome for outcome, _ in attempts] == ["deferred"], attempts
        assert _instant(attempts[0][1]).timestamp() == reset
        _sweep(hub, "close_drain")
        assert _close_attempts(hub_dir, number_b) == attempts
        assert _closure_facts(hub_dir, chunk_b) == 0
        assert _pending_intents(hub_dir, chunk_b) == 1
        assert forge.get(f"/repos/{REPO}/issues/{number_b}").json()["state"] == "open"

        # Many turn-aways, one notice for the window.
        _poll_until(lambda: _events(hub, "forge-budget-low"))
        time.sleep(2)
        [notice] = _events(hub, "forge-budget-low")
        assert notice["severity"] == "warning"
        assert _events(hub, "forge-rate-limited") == []


@pytest.mark.parametrize("lever", ["rate_limited", "secondary_rate_limited"])
def test_a_rate_refused_landing_pends_until_the_refusal_lifts_then_lands(tmp_path: Path, lever: str) -> None:
    bin_dir, workspace, origins = _world(tmp_path)
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        assert hub.post("/api/graphs", json={"definition_yaml": graph_yaml()}).status_code == 201
        chunk_id, _number = _ingest(hub, forge, f"land past a {lever} refusal")

        # Both refusals name a lift in Retry-After. A primary one also answers no remaining, which the budget learns:
        # it holds every call to the window's reset, so the reset is armed past the Retry-After.
        reset = int(time.time()) + 90
        assert forge.post("/_levers/rate_budget", json={"reset": reset}).status_code == 200
        assert forge.post(f"/_levers/{lever}", json={}).status_code == 200
        armed_at = time.time()

        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        pending = _drive_until(config, hub, chunk_id, lambda b: b["pending"] is not None, 120)
        refused_by = time.time()
        assert pending["status"] == "delivering", pending["status"]
        assert pending["landed"] is False
        assert "LANDED.md" not in _main_tree(origins)
        retry_after = _instant(pending["pending"]["next_poll_at"]).timestamp()
        assert armed_at + _RETRY_AFTER - 1 <= retry_after <= refused_by + _RETRY_AFTER + 1
        assert forge.delete(f"/_levers/{lever}").status_code == 200

        # The forge would answer now, but the spent budget holds every call until the refusal lifts.
        polls: list[float] = []

        def landed(body: dict[str, Any]) -> bool:
            if body["pending"] is not None:
                at = _instant(body["pending"]["next_poll_at"]).timestamp()
                if at not in polls:
                    polls.append(at)
            return body["status"] in {"done", "stopped", "needs_human"}

        done = _drive_until(config, hub, chunk_id, landed, 150)
        lift = reset if lever == "rate_limited" else retry_after
        assert polls[-1] == pytest.approx(lift, abs=1), (reset, polls)
        assert time.time() >= lift - 1, "the landing went out before the refusal lifted"
        assert done["status"] == "done", done["status"]
        assert "LANDED.md" in _main_tree(origins), "the landing did not reach the bare origin's main"

        _poll_until(lambda: _events(hub, "forge-rate-limited"))
        time.sleep(2)
        [notice] = _events(hub, "forge-rate-limited")
        assert notice["severity"] == "warning"
