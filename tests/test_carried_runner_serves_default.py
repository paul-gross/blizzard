"""A runner carried from before tenants and projects, whose configuration names no projects, keeps serving the
hub's ``default`` project with no configuration change: the store migrated to the head declares ``default`` for it,
the runner's registration sends no list, and the hub keeps the declaration and offers it ``default``'s work.

The carried store is the pre-tenant configuration fixture of ``tests/test_carried_config_round_trip.py`` plus one
runner that had registered before tenants, seeded with literal SQL at that revision."""

from __future__ import annotations

import dataclasses
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.store.engine import create_engine_from_url
from blizzard.hub.config import HubConfig
from blizzard.hub.runtime import Migrations
from blizzard.runner.config import RunnerConfig
from blizzard.runner.hub.internal.http_hub import HttpHubClient
from tests.support import build_hub
from tests.test_carried_config_round_trip import _AT, _BEFORE_TENANTS, _NOW, _SEED

pytestmark = pytest.mark.component

_RUNNER_ID = "run_carried"
_CARRIED_RUNNER = (
    "INSERT INTO runner_registrations (runner_id, name, workspace_id, registered_at, last_seen_at, added_at, added_by)"
    f" VALUES ('{_RUNNER_ID}', 'r-carried', 'ws-carried', {_AT}, {_AT}, {_AT}, 'operator')"
)


def _carried_store(root: Path) -> None:
    root.mkdir()
    db_url = f"sqlite:///{root / 'hub.db'}"
    Migrations(HubConfig(root=root, db_url=db_url), FixedClock(_NOW)).runner.upgrade(_BEFORE_TENANTS)
    engine = create_engine_from_url(db_url)
    try:
        with engine.begin() as conn:
            for statement in (*_SEED, _CARRIED_RUNNER):
                conn.execute(sa.text(statement))
    finally:
        engine.dispose()


def _carried_config(root: Path, hub_url: str) -> RunnerConfig:
    """The runner's configuration as written before projects: no ``projects`` key at all."""
    root.mkdir(parents=True)
    config = dataclasses.replace(RunnerConfig.scaffold(root, hub_url=hub_url), projects=None)
    config.config_path.write_text(config.to_toml())
    assert "\nprojects =" not in config.config_path.read_text()
    return RunnerConfig.load(root)


def test_a_carried_runner_naming_no_projects_keeps_serving_default(tmp_path: Path) -> None:
    _carried_store(tmp_path / "hub")
    hub = build_hub(tmp_path / "hub")
    assert hub.app is not None
    (default,) = hub.client.get("/api/projects").json()["projects"]
    assert default["slug"] == "default"
    enrolled = hub.client.post(f"/api/runners/{_RUNNER_ID}/enrollments")
    assert enrolled.status_code == 201, enrolled.text
    bearer = {"Authorization": f"Bearer {enrolled.json()['token']}"}
    config = _carried_config(tmp_path / "runner", str(hub.client.base_url))
    assert config.projects is None

    with TestClient(hub.app, headers=bearer) as fleet:
        reply = HttpHubClient(fleet).register_runner(config.name, "ws-carried", projects=config.projects)

    assert reply.runner_id == _RUNNER_ID
    assert reply.projects is not None
    assert [(p.entry, p.project_id, p.slug) for p in reply.projects] == [
        (default["project_id"], default["project_id"], "default")
    ]
    detail = hub.client.get(f"/api/runners/{_RUNNER_ID}")
    assert detail.status_code == 200, detail.text
    assert [(p["entry"], p["project_id"]) for p in detail.json()["projects"]] == [
        (default["project_id"], default["project_id"])
    ]

    ingested = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert ingested.status_code == 201, ingested.text
    chunk_id = ingested.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202
    peeked = hub.client.post(
        "/api/fleet/queue/peek", json={"capabilities": [{"harness_id": "claude", "default": True}]}, headers=bearer
    )
    assert peeked.status_code == 200, peeked.text
    assert [(e["chunk_id"], e["project_slug"]) for e in peeked.json()["entries"]] == [(chunk_id, "default")]
