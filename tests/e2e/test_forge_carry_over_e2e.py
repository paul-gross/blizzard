"""A store held before forges were records carries over, and a chunk on it lands as one landed before.

A HEAD hub is seeded through its doors with the old address fields — the fixture work source's ``api_base`` and its
repository's ``forge_api_url``, both the mock forge's ``http://127.0.0.1:<port>`` — and lands one chunk. The store walks
down to ``20261024_0900_hub_legacy_run``, where no forge exists, and the HEAD migrate and hub carry it: both records name
one forge for that origin, its port kept, and a second chunk lands against the mock forge with the same outcome and
markers as the first. Needs ``BLIZZARD_E2E=1`` and ``blizzard-mock``."""

from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path
from typing import Any

import httpx2
import pytest
import yaml

from tests.fleet_launchers import REPO, drive_until_done, mock_bin_dir, run_forge, run_hub, runner_config
from tests.fleet_world import FIXTURE_ENV, PUSH_AND_DECLARE_SCRIPT, REPO_NAME, mint_world, scripted_build_prompt
from tests.support import free_port

pytestmark = [
    pytest.mark.e2e,
    pytest.mark.skipif(
        os.environ.get("BLIZZARD_E2E") != "1",
        reason="e2e forge carry-over needs the live stack; set BLIZZARD_E2E=1 (see module docstring)",
    ),
]

#: The chain's head before the forge migration.
_BEFORE_FORGES = "20261024_0900_hub_legacy_run"
_GRAPH = "default-delivery"
_LAND_CHOICES = {
    "landed": {"description": "Every repo's PR merged cleanly.", "to": "done"},
    "conflict": {"description": "A repo's PR is dirty; back to build.", "to": "build"},
    "failure": {"description": "A repo could not land; back to build.", "to": "build"},
    "inherited-failure": {"description": "The base check stays red; back to build.", "to": "build"},
}


def _graph(landed_file: str) -> str:
    """A graph whose build commits ``landed_file`` and whose hub node lands it."""
    build = scripted_build_prompt(
        "import subprocess, pathlib\n"
        f"repo = {REPO_NAME!r}\n"
        f'(pathlib.Path(repo) / {landed_file!r}).write_text("carried over\\n")\n'
        'subprocess.run(["git", "-C", repo, "add", "-A"], check=True)\n'
        "subprocess.run(\n"
        '    ["git", "-C", repo,\n'
        '     "-c", "user.email=mock@blizzard.local", "-c", "user.name=Mock Harness",\n'
        f'     "commit", "-m", "feat: {landed_file}"],\n'
        "    check=True,\n"
        ")\n" + PUSH_AND_DECLARE_SCRIPT
    )
    graph = {
        "name": _GRAPH,
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
    minted = hub.post("/api/graphs", json={"definition_yaml": _graph(landed_file)})
    assert minted.status_code == 201, minted.text
    issue = forge.post(f"/repos/{REPO}/issues", json={"title": landed_file, "body": f"lands {landed_file}"})
    assert issue.status_code == 201, issue.text
    ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{issue.json()['number']}"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.get(f"/api/chunks/{chunk_id}").json()["graph_id"] == minted.json()["graph_id"]
    return chunk_id


def _hub_migrate(hub_dir: Path, *args: str) -> subprocess.CompletedProcess[str]:
    hub_bin = str(Path(sys.executable).parent / "blizzard-hub")
    done = subprocess.run([hub_bin, "migrate", "--dir", str(hub_dir), *args], capture_output=True, text=True)
    assert done.returncode == 0, done.stderr
    return done


def _store(hub_dir: Path) -> sqlite3.Connection:
    return sqlite3.connect(hub_dir / "data" / "hub.db")


def _forge_naming(hub: httpx2.Client) -> set[tuple[str, str, str]]:
    """Every repository's and work source's forge, as ``(forge_id, forge_slug, api origin)``."""
    repositories = hub.get("/api/repositories").json()["repositories"]
    sources = [s for s in hub.get("/api/work-sources").json()["sources"] if not s["built_in"]]
    return {(r["forge_id"], r["forge_slug"], r["forge_api_url"]) for r in repositories} | {
        (s["forge_id"], s["forge_slug"], s["api_base"]) for s in sources
    }


def _landing(detail: dict[str, Any]) -> tuple[list[str], bool, list[str]]:
    """What a chunk's landing shows: its deliver choices, whether it landed, and its marker kinds — each artifact's name
    up to its first ``/``, since a marker names its own PR."""
    choices = [h["choice_name"] for h in detail["history"] if h.get("from_node_name") == "deliver"]
    markers = sorted({a["name"].partition("/")[0] for a in detail["artifacts"] if "/" in a["name"]})
    return choices, detail["landed"], markers


def test_a_store_held_before_forges_carries_over_and_lands_a_chunk_as_before(tmp_path: Path) -> None:
    bin_dir = mock_bin_dir()
    if bin_dir is None:
        pytest.skip("no provisioned sibling blizzard-mock worktree (run `winter provision <env>`)")
    mint_world(bin_dir, tmp_path / "scratch")
    fixture_root = tmp_path / "scratch" / FIXTURE_ENV
    workspace, origins = fixture_root / "workspace", fixture_root / "origins"
    hub_dir = tmp_path / "hub"
    fenced = {**os.environ, "BLIZZARD_MOCK_HARNESS_FENCE": "1"}
    forge_port, hub_port = free_port(), free_port()
    origin = f"http://127.0.0.1:{forge_port}"

    with run_forge(bin_dir, origins, forge_port) as forge:
        with run_hub(hub_dir, forge_port, hub_port, test_actions=False) as hub:
            before_forges = _forge_naming(hub)
            config = runner_config(tmp_path / "runner", workspace, bin_dir, hub_port)
            first = _pinned_chunk(hub, forge, "BEFORE.md")
            second = _pinned_chunk(hub, forge, "AFTER.md")
            assert hub.post(f"/api/chunks/{first}/promote").status_code == 202
            assert drive_until_done(config, hub, first, fenced, timeout=180.0) == "done"
            landed_before = hub.get(f"/api/chunks/{first}").json()
        [(_, slug, api_base)] = before_forges
        assert (slug, api_base) == (f"127-0-0-1-{forge_port}", origin)

        _hub_migrate(hub_dir, "--down", _BEFORE_FORGES)
        with _store(hub_dir) as conn:
            assert conn.execute("SELECT name FROM sqlite_master WHERE name = 'forges'").fetchone() is None
            held = conn.execute("SELECT forge_api_url FROM repositories").fetchall()
        assert held == [(origin,)]

        _hub_migrate(hub_dir)
        with run_hub(hub_dir, forge_port, hub_port, test_actions=False, rehost=True) as hub:
            carried = _forge_naming(hub)
            assert hub.post(f"/api/chunks/{second}/promote").status_code == 202
            status = drive_until_done(config, hub, second, fenced, timeout=180.0)
            landed_after = hub.get(f"/api/chunks/{second}").json()
            assert status == "done", (status, landed_after["history"])

    [(forge_id, slug, api_base)] = carried
    assert (slug, api_base) == (f"127-0-0-1-{forge_port}", origin)
    assert forge_id.startswith("frg_")
    assert _landing(landed_after) == _landing(landed_before)
    choices, landed, markers = _landing(landed_after)
    assert (choices, landed) == (["landed"], True)
    assert "merged" in markers, markers
