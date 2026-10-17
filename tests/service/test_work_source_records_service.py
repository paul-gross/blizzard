"""A running hub reads its work source from the record created through its own API.

``run_hub`` seeds the one fixture source and its secret through ``/api/secrets`` and
``/api/work-sources`` once the daemon answers; ingest then reads the real ``blizzard-mock``
forge through it, and an edit made while the hub runs reaches the next sweep pass."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from tests.fleet_launchers import REPO, run_forge, run_hub
from tests.fleet_world import REPO_NAME, mint_world
from tests.service.support import poll_until, require_mock_fleet, service_gate
from tests.support import free_port

pytestmark = [pytest.mark.service, service_gate]


def _forge_labels(forge: httpx.Client, number: int) -> set[str]:
    resp = forge.get(f"/repos/{REPO}/issues/{number}")
    assert resp.status_code == 200, resp.text
    return {label["name"] for label in resp.json()["labels"]}


def test_a_source_created_through_the_api_ingests_from_the_mock_forge_and_takes_edits_live(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    forge_port, hub_port = free_port(), free_port()
    with (
        run_forge(bin_dir, origins, forge_port) as forge,
        run_hub(tmp_path / "hub", forge_port, hub_port, annotation_interval_seconds=1) as hub,
    ):
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "read me through the record", "body": "b"})
        assert issue.status_code == 201, issue.text
        number = issue.json()["number"]

        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]
        items = hub.get(f"/api/chunks/{chunk_id}/work-items")
        assert items.status_code == 200, items.text
        assert [item["title"] for item in items.json()["items"]] == ["read me through the record"]
        assert hub.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

        edited = hub.patch(f"/api/work-sources/{REPO_NAME}", json={"annotate": True})
        assert edited.status_code == 200, edited.text

        assert poll_until(lambda: "blizzard:ingested" in _forge_labels(forge, number), timeout=15.0), (
            f"label never appeared after the edit: {_forge_labels(forge, number)}"
        )


def test_a_renamed_source_keeps_its_refs_and_renders_them_under_its_new_slug(tmp_path: Path) -> None:
    bin_dir = require_mock_fleet()
    _workspace, origins, _bare = mint_world(bin_dir, tmp_path / "scratch")
    forge_port, hub_port = free_port(), free_port()
    with run_forge(bin_dir, origins, forge_port) as forge, run_hub(tmp_path / "hub", forge_port, hub_port) as hub:
        issue = forge.post(f"/repos/{REPO}/issues", json={"title": "follow the rename", "body": "b"})
        assert issue.status_code == 201, issue.text
        number = issue.json()["number"]
        ingested = hub.post("/api/chunks", json={"tokens": [f"{REPO_NAME}:{number}"]})
        assert ingested.status_code == 201, ingested.text
        chunk_id = ingested.json()["chunk_id"]

        renamed = hub.patch(f"/api/work-sources/{REPO_NAME}", json={"slug": "renamed", "name": "Renamed source"})
        assert renamed.status_code == 200, renamed.text
        source_id = renamed.json()["work_source_id"]

        (pointer,) = hub.get(f"/api/chunks/{chunk_id}").json()["work_refs"]
        assert (pointer["source"], pointer["label"]) == (source_id, f"renamed#{number}")
        (item,) = hub.get(f"/api/chunks/{chunk_id}/work-items").json()["items"]
        assert (item["slug"], item["title"]) == ("renamed", "follow the rename")

        for token in (f"{REPO_NAME}:{number}", f"renamed:{number}", f"{source_id}:{number}"):
            held = hub.post("/api/chunks", json={"tokens": [token]})
            assert held.status_code == 409, (token, held.text)
            assert (held.json()["existing_chunk_id"], held.json()["label"]) == (chunk_id, f"renamed#{number}")
        assert hub.get(f"/api/work-sources/{REPO_NAME}").json()["work_source_id"] == source_id
