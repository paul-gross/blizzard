"""The hub's flow driver keeps a live hub's flow lines true to where its chunks stand, taking each hub step as a turn.

A chunk held pending at ``deliver`` — the mock forge's ``checks_pending`` lever armed — is submitted to its branch's
flow and polls through turns; the turn that lands it once the lever clears closes that submission ``landed``.
Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import os
import sqlite3
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop_wiring import LoopWiring
from tests.fleet_launchers import REPO, mock_bin_dir, run_forge, run_hub, runner_api, runner_config
from tests.fleet_world import (
    FIXTURE_ENV,
    PUSH_AND_DECLARE_SCRIPT,
    REPO_NAME,
    mint_world,
    scripted_build_prompt,
)
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e flow record needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: Comfortably past the driver's two-second tick, so a converged line is never a race.
_DRIVER_WAIT = 30.0
_LAND_CHOICES = {
    "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
    "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
    "failure": {"description": "A repo could not land; back to build.", "to": "build"},
    "inherited-failure": {"description": "The base check stays red; back to build.", "to": "build"},
}


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
                "judgement": {"choices": _LAND_CHOICES},
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


def _drive_until(
    config: RunnerConfig, hub: httpx2.Client, chunk_id: str, predicate: Callable[[dict[str, Any]], bool]
) -> dict[str, Any]:
    """Tick the runner until ``predicate`` holds of the chunk's detail; return that detail."""
    prior = dict(os.environ)
    os.environ["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    try:
        with runner_api(config):
            deadline = time.monotonic() + 120.0
            while time.monotonic() < deadline:
                LoopWiring.of(config).tick_once()
                detail = hub.get(f"/api/chunks/{chunk_id}").json()
                if predicate(detail):
                    return detail
                time.sleep(0.4)
            raise AssertionError(f"chunk {chunk_id} never reached the awaited state")
    finally:
        os.environ.clear()
        os.environ.update(prior)


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


def _world(tmp_path: Path) -> tuple[Path, Path, Path]:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    mint_world(bin_dir, tmp_path / "scratch")
    fixture_root = tmp_path / "scratch" / FIXTURE_ENV
    return bin_dir, fixture_root / "workspace", fixture_root / "origins"


def test_a_held_chunk_is_submitted_and_its_landing_supersedes_it(tmp_path: Path) -> None:
    bin_dir, workspace, origins = _world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(hub_dir, forge_port, hub_port) as hub:
        assert forge.post("/_levers/checks_pending", json={"repo": REPO}).status_code == 200
        config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
        chunk_id = _pinned_chunk(hub, forge, "HELD.md")
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        _drive_until(config, hub, chunk_id, _pending)
        [(_, submitted, _, open_outcome)] = _await_submissions(hub_dir, bool)
        assert (submitted, open_outcome) == (chunk_id, None)

        assert forge.delete("/_levers/checks_pending", params={"repo": REPO}).status_code == 200
        done = _drive_until(config, hub, chunk_id, lambda b: b["status"] in {"done", "needs_human"})
        assert (done["status"], done["landed"]) == ("done", True)
        [(_, closed, _, outcome)] = _await_submissions(hub_dir, lambda rows: bool(rows) and rows[0][3] is not None)
        assert (closed, outcome) == (chunk_id, "landed")
        with _store(hub_dir) as conn:
            assert conn.execute("SELECT count(*) FROM hub_turns WHERE ended_at IS NULL").fetchone() == (0,)
