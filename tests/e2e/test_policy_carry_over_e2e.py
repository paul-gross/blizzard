"""A store held before repositories had landing policies carries over, and its chunks land as they landed before.

A HEAD hub is seeded through its doors and lands one single-repository chunk; a second waits mid-delivery behind
``checks_pending``. The store walks down to ``20261029_0900_close_attempt_resume_at``, where no repository holds a
policy, no project an order, and no submission its terms; the HEAD migrate and hub carry it back up: every repository
reads ``merge-on-checks`` and every project its links unranked. Once the lever is released the waiting chunk lands, a
third chunk lands after it, and both show the outcome and markers the first did. The landing invariants hold on the
carried-over store and at the end. Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest

from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.tools.invariants import hub_invariants
from tests.e2e.flow_support import flow_runner_config, flow_world, pinned_chunk, set_lever, ticking_runner
from tests.e2e.test_flow_turns_e2e import (
    await_landed,
    chunk_detail,
    pending,
)
from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e policy carry-over needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: The chain's head before repositories carried a landing policy.
_BEFORE_POLICIES = "20261029_0900_close_attempt_resume_at"
_LANDING_INVARIANTS = frozenset(
    {"hub:landing-position-on-link", "hub:submission-terms-standing", "hub:repository-references-in-tenant"}
)


def _hub_migrate(hub_dir: Path, *args: str) -> None:
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    done = subprocess.run([hub_bin, "migrate", "--dir", str(hub_dir), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr


def _store(hub_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(hub_dir / "data" / "hub.db")


def _columns(hub_dir: Path, table: str) -> set[str]:
    with _store(hub_dir) as conn:
        return {row[1] for row in conn.execute(f"PRAGMA table_info({table})").fetchall()}


def _tables(hub_dir: Path) -> set[str]:
    with _store(hub_dir) as conn:
        return {name for (name,) in conn.execute("SELECT name FROM sqlite_master WHERE type = 'table'").fetchall()}


def _landing(detail: dict[str, Any]) -> tuple[list[str], bool, list[str]]:
    """What a chunk's landing shows: its deliver choices, whether it landed, and its marker kinds — each artifact's name
    up to its first ``/``, since a marker names its own PR."""
    choices = [h["choice_name"] for h in detail["history"] if h.get("from_node_name") == "deliver"]
    markers = sorted({a["name"].partition("/")[0] for a in detail["artifacts"] if "/" in a["name"]})
    return choices, detail["landed"], markers


def _policies(hub: httpx2.Client) -> set[tuple[str, str, int | None]]:
    """Every repository as ``(slug, landing_policy, park_timeout_hours)``."""
    return {
        (r["slug"], r["landing_policy"], r["park_timeout_hours"])
        for r in hub.get("/api/repositories").json()["repositories"]
    }


def _positions(hub: httpx2.Client) -> set[tuple[str, str, float | None]]:
    """Every project's standing links as ``(project, repository, position)``."""
    return {
        (project["slug"], link["slug"], link["position"])
        for project in hub.get("/api/projects").json()["projects"]
        for link in hub.get(f"/api/projects/{project['slug']}/repository-links").json()["repository_links"]
    }


def _assert_landing_record_holds(hub_dir: Path) -> None:
    """The landing position, the submission terms, and the tenant's repository references are all sound."""
    engine = create_engine_from_url(f"sqlite:///{hub_dir / 'data' / 'hub.db'}")
    try:
        violations = [v for v in hub_invariants(engine).run() if v.invariant in _LANDING_INVARIANTS]
    finally:
        engine.dispose()
    assert violations == []


def test_a_store_held_before_landing_policies_carries_over_and_lands_its_chunks_as_before(tmp_path: Path) -> None:
    bin_dir, workspace, origins = flow_world(tmp_path)
    hub_dir = tmp_path / "hub"
    forge_port, hub_port = free_port(), free_port()

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port, test_actions=False) as hub:
            config = flow_runner_config(tmp_path, workspace, bin_dir, hub_port)
            with ticking_runner(config) as tick_until:
                before = pinned_chunk(hub, forge, "BEFORE.md")
                tick_until(lambda: chunk_detail(hub, before)["status"] == "done")
                landed_before = chunk_detail(hub, before)
                set_lever(forge, REPO, armed=True)
                midway = pinned_chunk(hub, forge, "MIDWAY.md")
                tick_until(pending(hub, midway))
        assert (landed_before["status"], landed_before["landed"]) == ("done", True)

        _hub_migrate(hub_dir, "--down", _BEFORE_POLICIES)
        assert {"landing_policy", "park_timeout_hours"}.isdisjoint(_columns(hub_dir, "repositories"))
        assert {"project_repository_positions", "flow_submission_terms"}.isdisjoint(_tables(hub_dir))

        _hub_migrate(hub_dir)
        _assert_landing_record_holds(hub_dir)
        with run_hub(hub_dir, forge_port, hub_port, test_actions=False, rehost=True) as hub:
            assert _policies(hub) == {(REPO_NAME, "merge-on-checks", None)}
            assert _positions(hub) == {("default", REPO_NAME, None)}
            assert chunk_detail(hub, midway)["status"] == "delivering"

            set_lever(forge, REPO, armed=False)
            await_landed(hub, midway)
            landed_midway = chunk_detail(hub, midway)

            with ticking_runner(config) as tick_until:
                after = pinned_chunk(hub, forge, "AFTER.md")
                tick_until(lambda: chunk_detail(hub, after)["status"] in {"done", "needs_human", "stopped"})
            await_landed(hub, after)
            landed_after = chunk_detail(hub, after)

    assert _landing(landed_midway) == _landing(landed_before)
    assert _landing(landed_after) == _landing(landed_before)
    choices, landed, markers = _landing(landed_before)
    assert (choices, landed) == (["landed"], True)
    assert "merged" in markers, markers
    _assert_landing_record_holds(hub_dir)
