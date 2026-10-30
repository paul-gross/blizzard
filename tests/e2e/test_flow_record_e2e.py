"""The hub's flow driver keeps a live hub's flow lines true to where its chunks stand, taking each hub step as a turn.

A store walked below ``20261027_0900_flows`` with two chunks pending and one paused at ``deliver``, migrated back and
booted, lands every one. Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.runner.config import RunnerConfig
from blizzard.tools.invariants import hub_invariants
from tests.e2e.flow_support import LAND_CHOICES, flow_runner_config, flow_world, set_lever, ticking_runner
from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.fleet_world import PUSH_AND_DECLARE_SCRIPT, REPO_NAME, scripted_build_prompt
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e flow record needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: The chain's head before the flows revision.
_BEFORE_FLOWS = "20261025_0900_forges"
_FLOW_INVARIANTS = frozenset({"hub:one-turn-per-flow", "hub:one-turn-per-visit", "hub:flow-line-standing"})
#: Comfortably past the driver's two-second tick, so a converged line is never a race.
_DRIVER_WAIT = 30.0


def _graph(landed_file: str) -> str:
    """A graph whose build commits ``landed_file`` on a branch of its own and whose hub node lands it, re-polling
    every second."""
    build = scripted_build_prompt(
        "import subprocess, pathlib\n"
        f"repo = {REPO_NAME!r}\n"
        f'(pathlib.Path(repo) / {landed_file!r}).write_text("held on a flow\\n")\n'
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "subprocess.run(\n"
        '    ["git", "-C", repo,\n'
        '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        f'     "commit", "-m", "feat: {landed_file}"],\n'
        "    check=True,\n"
        ")\n" + PUSH_AND_DECLARE_SCRIPT,
        branch=f"feature/{Path(landed_file).stem.lower()}",
    )
    graph = {
        "name": "default-delivery",
        "entry": "build",
        "nodes": {
            "build": {
                "executor": "runner",
                "prompt": build,
                "judgement": {
                    "prompt": "verdict('pass', 'the mock harness did its turn')\n",
                    "choices": {"pass": {"description": "Built.", "to": "deliver"}},
                },
                "retries": {"max": 1, "exhausted": "escalate"},
            },
            "deliver": {
                "executor": "hub",
                "action": "land",
                "poll_interval": 1,
                "poll_timeout": 600,
                "judgement": {"choices": LAND_CHOICES},
            },
        },
    }
    return yaml.safe_dump(graph, sort_keys=False)


def _pinned_chunk(hub: httpx2.Client, forge: httpx2.Client, landed_file: str) -> str:
    """Mint the graph landing ``landed_file`` and ingest one chunk pinned to it."""
    assert hub.post("/api/graphs", json={"definition_yaml": _graph(landed_file)}).status_code == 201
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": f"lands {landed_file}"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    return ingested.json()["chunk_id"]


def _detail(hub: httpx2.Client, chunk_id: str) -> dict[str, Any]:
    return hub.get(f"/api/chunks/{chunk_id}").json()


def _pending(detail: dict[str, Any]) -> bool:
    return detail["pending"] is not None


def _store(hub_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(hub_dir / "data" / "hub.db")


def _submissions(hub_dir: Path) -> list[tuple[str, str, int, str | None]]:
    """Every submission as ``(submission_id, chunk_id, arrival, closure outcome)``, in arrival order."""
    with _store(hub_dir) as conn:
        return conn.execute(
            "SELECT s.submission_id, s.chunk_id, s.arrival, c.outcome FROM flow_submissions s"
            " LEFT JOIN flow_submission_closures c ON c.submission_id = s.submission_id ORDER BY s.arrival"
        ).fetchall()


def _await_submissions(hub_dir: Path, until: Callable[[list[tuple[str, str, int, str | None]]], bool]) -> list:
    """Wait on the hub's own flow driver until its submissions satisfy ``until``; return them."""
    deadline = time.monotonic() + _DRIVER_WAIT
    while time.monotonic() < deadline:
        rows = _submissions(hub_dir)
        if until(rows):
            return rows
        time.sleep(0.5)
    raise AssertionError(f"the flow driver never converged: {_submissions(hub_dir)}")


def _assert_flow_record_holds(hub_dir: Path) -> None:
    """The three flow invariants hold over every turn taken so far."""
    engine = create_engine_from_url(f"sqlite:///{hub_dir / 'data' / 'hub.db'}")
    try:
        violations = [v for v in hub_invariants(engine).run() if v.invariant in _FLOW_INVARIANTS]
    finally:
        engine.dispose()
    assert violations == []


def _entered_deliver(detail: dict[str, Any]) -> str:
    return next(h["recorded_at"] for h in detail["history"] if h.get("to_node_name") == "deliver")


def _hub_migrate(hub_dir: Path, *args: str) -> None:
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    done = subprocess.run([hub_bin, "migrate", "--dir", str(hub_dir), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _completion_buffered(config: RunnerConfig) -> bool:
    with sqlite3.connect(Path(config.db_url.removeprefix("sqlite:///"))) as conn:
        return conn.execute(
            "SELECT count(*) FROM outbound_buffer WHERE kind = 'completion.submitted' AND acked_at IS NULL"
        ).fetchone() != (0,)


def _at_deliver(detail: dict[str, Any]) -> bool:
    return any(h.get("to_node_name") == "deliver" for h in detail["history"])


def _await_settled(hub: httpx2.Client, *chunk_ids: str) -> None:
    deadline = time.monotonic() + 2 * _DRIVER_WAIT
    while time.monotonic() < deadline:
        if all(_detail(hub, c)["status"] in {"done", "needs_human", "stopped"} for c in chunk_ids):
            return
        time.sleep(0.5)
    raise AssertionError(f"the flow driver never landed {chunk_ids}")


def _assert_landed(hub: httpx2.Client, hub_dir: Path, chunk_id: str) -> None:
    """The chunk is ``done`` by a ``landed`` choice, carries its ``merged/<repo>`` marker, and has its landing row."""
    detail = _detail(hub, chunk_id)
    assert (detail["status"], detail["landed"]) == ("done", True), (chunk_id, detail["history"])
    assert detail["history"][-1]["choice_name"] == "landed", detail["history"]
    assert any(a["name"].startswith("merged/") and a["name"].endswith(REPO_NAME) for a in detail["artifacts"])
    with _store(hub_dir) as conn:
        landed = conn.execute("SELECT repo FROM delivery_repo_landed WHERE chunk_id = ?", (chunk_id,)).fetchall()
    assert [repo for (repo,) in landed if repo.endswith(REPO_NAME)], landed


def test_a_store_walked_below_flows_submits_its_standing_chunks_in_deliver_order_and_lands_every_one(
    tmp_path: Path,
) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port) as hub:
            set_lever(forge, REPO, armed=True)
            config = flow_runner_config(tmp_path, workspace, bin_dir, hub_port, environments=3)
            # Minted second-first, so the chunks' id order is the reverse of the order they enter ``deliver``.
            second = _pinned_chunk(hub, forge, "SECOND.md")
            first = _pinned_chunk(hub, forge, "FIRST.md")
            paused = _pinned_chunk(hub, forge, "PAUSED.md")
            with ticking_runner(config) as tick_until:
                assert hub.post(f"/api/chunks/{first}/promote").status_code == 202
                tick_until(lambda: _pending(_detail(hub, first)))
                assert hub.post(f"/api/chunks/{second}/promote").status_code == 202
                tick_until(lambda: _pending(_detail(hub, second)))
                assert hub.post(f"/api/chunks/{paused}/promote").status_code == 202
                tick_until(lambda: _completion_buffered(config))
                assert hub.post(f"/api/chunks/{paused}/pause", json={"by": "e2e"}).status_code == 202
                tick_until(lambda: _at_deliver(_detail(hub, paused)))
            held = {c: _detail(hub, c) for c in (first, second, paused)}
        assert _entered_deliver(held[first]) < _entered_deliver(held[second]) < _entered_deliver(held[paused])
        assert second < first
        assert held[paused]["status"] == "paused"

        _hub_migrate(hub_dir, "--down", _BEFORE_FLOWS)
        with _store(hub_dir) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'flow_submissions'").fetchone() is None

        _hub_migrate(hub_dir)
        with run_hub(hub_dir, forge_port, hub_port, rehost=True) as hub:
            rows = _await_submissions(hub_dir, lambda found: len(found) == 2)
            assert [(chunk, outcome) for _, chunk, _, outcome in rows] == [(first, None), (second, None)]
            assert [_detail(hub, c)["status"] for c in (first, second, paused)] == [
                "delivering",
                "delivering",
                "paused",
            ]
            _assert_flow_record_holds(hub_dir)

            set_lever(forge, REPO, armed=False)
            _await_settled(hub, first, second)
            assert _detail(hub, paused)["status"] == "paused"
            _assert_flow_record_holds(hub_dir)

            assert hub.post(f"/api/chunks/{paused}/resume", json={"by": "e2e"}).status_code == 202
            _await_settled(hub, paused)
            for chunk_id in (first, second, paused):
                _assert_landed(hub, hub_dir, chunk_id)
            landed = {chunk for _, chunk, _, outcome in _submissions(hub_dir) if outcome == "landed"}
            assert landed == {first, second, paused}
            with _store(hub_dir) as conn:
                assert conn.execute("SELECT count(*) FROM hub_turns WHERE ended_at IS NULL").fetchone() == (0,)
            _assert_flow_record_holds(hub_dir)
