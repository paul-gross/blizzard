"""The forge-status annotation loop against a real running hub.

A real hosted hub, opted into ``annotate = true``, drives its background sweep against
the real ``blizzard-mock`` forge; a hub with no opted-in source is the negative control —
the loop must not start at all, so no label ever appears."""

from __future__ import annotations

import time
from pathlib import Path

import httpx2
import pytest

from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME, mint_world
from tests.service.support import poll_until, require_mock_fleet, service_gate
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]


def _stack(tmp_path: Path) -> tuple[Path, Path, int, int]:
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    forge_port, hub_port = free_port(), free_port()
    return bin_dir, origins, forge_port, hub_port


def _forge_labels(forge: httpx2.Client, number: int) -> set[str]:
    resp = forge.get(f"/repos/{REPO}/issues/{number}")
    assert resp.status_code == 200, resp.text
    return {label["name"] for label in resp.json()["labels"]}


def test_an_opted_in_source_sweeps_the_real_forge(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, annotate=True, annotation_interval_seconds=1) as hub,
    ):
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "sweep me", "body": "b"})
        assert issue.status_code == 201, issue.text
        number = issue.json()["number"]

        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        assert poll_until(lambda: "blizzard:ingested" in _forge_labels(forge, number), timeout=15.0), (
            f"label never appeared: {_forge_labels(forge, number)}"
        )


def test_a_non_opted_in_source_starts_no_sweep_loop(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, annotation_interval_seconds=1) as hub,
    ):
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "don't sweep me", "body": "b"})
        assert issue.status_code == 201, issue.text
        number = issue.json()["number"]

        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        # Several sweep intervals' worth of real time, with nothing opted-in to sweep.
        time.sleep(3)

        assert _forge_labels(forge, number) == set()
