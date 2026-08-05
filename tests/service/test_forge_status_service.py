"""The forge-status annotation loop against a real running hub (issue #179, Phase 5).

A real hosted hub, opted into ``annotate = true``, drives its background sweep against
the real ``blizzard-mock`` forge; a hub with no opted-in source is the negative control —
the loop must not start at all, so no label ever appears."""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from tests.e2e.test_acceptance_loop import REPO, REPO_NAME, _forge, _free_port, _hub
from tests.service.support import mint_fixture, poll_until, require_mock_fleet, require_winter_source, service_gate

pytestmark = [pytest.mark.service, service_gate]


def _stack(tmp_path: Path) -> tuple[Path, Path, int, int]:
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_fixture(bin_dir, require_winter_source(), tmp_path / "scratch")
    forge_port, hub_port = _free_port(), _free_port()
    return bin_dir, origins, forge_port, hub_port


def _forge_labels(forge: httpx.Client, number: int) -> set[str]:
    resp = forge.get(f"/repos/{REPO}/issues/{number}")
    assert resp.status_code == 200, resp.text
    return {label["name"] for label in resp.json()["labels"]}


def test_an_opted_in_source_sweeps_the_real_forge(tmp_path: Path) -> None:
    bin_dir, origins, forge_port, hub_port = _stack(tmp_path)
    with (
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, annotate=True, annotation_interval_seconds=1) as hub,
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
        _forge(bin_dir, origins, forge_port) as forge,
        _hub(tmp_path / "hub", forge_port, hub_port, annotation_interval_seconds=1) as hub,
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
