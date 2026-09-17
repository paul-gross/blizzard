"""The OpenCode binding, service tier: the real runner against the mock hub, running its
worker turns under ``mock-opencode``. ``build`` mints fresh and resumes for judgement;
``review`` then shares that SAME session pool and resumes the identical session twice
more — the cross-node resume every harness's session pools give alike."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx
import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.loop.build import LoopWiring
from tests.e2e.test_acceptance_loop import _free_port, _runner_config
from tests.service.support import (
    mint_fixture,
    mock_hub,
    mock_hub_opencode_chunk_spec,
    poll_until,
    require_mock_fleet,
    require_winter_source,
    service_gate,
)

pytestmark = [pytest.mark.service, service_gate]

_WORK_REF_URL = "blizzard/toy-api/issues/1"


def _tick_env() -> dict[str, str]:
    fenced = dict(os.environ)
    fenced["BLIZZARD_MOCK_HARNESS_FENCE"] = "1"
    return fenced


def _drive(config: RunnerConfig, fenced: dict[str, str], *, ticks: int, pause: float = 0.5) -> None:
    prior = dict(os.environ)
    os.environ.update(fenced)
    try:
        for _ in range(ticks):
            LoopWiring.of(config).tick_once()
            time.sleep(pause)
    finally:
        os.environ.clear()
        os.environ.update(prior)


def _status(hub: httpx.Client, chunk_id: str) -> str:
    return hub.get(f"/api/fleet/chunks/{chunk_id}").json()["status"]


def _run_and_check(config: RunnerConfig, fenced: dict[str, str], hub: httpx.Client, chunk_id: str, target: str) -> bool:
    _drive(config, fenced, ticks=1, pause=0.3)
    return _status(hub, chunk_id) == target


def _seed_opencode(hub: httpx.Client) -> str:
    resp = hub.post("/_seed/chunk", json=mock_hub_opencode_chunk_spec(_WORK_REF_URL))
    assert resp.status_code == 201, resp.text
    return resp.json()["chunk_id"]


def _opencode_session_files(workspace: Path) -> list[Path]:
    """Every ``mock-opencode`` session state file this run minted — the mock's own on-disk
    record, keyed by the server-assigned session id no caller ever supplied."""
    return sorted((workspace / ".blizzard-mock-harness" / "sessions").glob("*.json"))


def test_opencode_build_and_review_resume_the_same_session_to_done(tmp_path: Path) -> None:
    """``build`` runs to completion on OpenCode, then ``review`` resumes that identical
    session rather than minting its own — asserted off the one session file
    ``mock-opencode`` ever wrote: spawn, then three resumes, in order."""
    bin_dir = require_mock_fleet()
    workspace, _origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    fenced = _tick_env()

    hub_port = _free_port()
    with mock_hub(bin_dir, hub_port) as hub:
        chunk_id = _seed_opencode(hub)
        config = _runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)

        landed = poll_until(lambda: _run_and_check(config, fenced, hub, chunk_id, "done"), timeout=90.0)
        assert landed, f"chunk did not land under OpenCode (status {_status(hub, chunk_id)!r})"

    # Exactly one OpenCode session was ever minted — `build` and `review` shared it, never
    # forking a second one, which is the whole point of the shared session-name pool.
    sessions = _opencode_session_files(workspace)
    assert len(sessions) == 1, f"expected exactly one OpenCode session, found {[p.name for p in sessions]}"
    state = json.loads(sessions[0].read_text())

    kinds = [invocation["kind"] for invocation in state["invocations"]]
    assert kinds == ["spawn", "resume", "resume", "resume"], (
        "build's fresh mint, build's judgement resume, review's node-entry resume, and "
        f"review's judgement resume, in order — got {kinds}"
    )
