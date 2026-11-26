"""Every operator route that records who acted records the authenticated caller, never a name the body supplies.

Each case signs in as a seeded user who is not ``operator`` and sends a body naming someone else, so a route that
read the body, or recorded a constant, fails here. A runner a host retires under itself is recorded as retired by
that host."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from blizzard.auth_core import Role
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_project,
    emitted_events,
    ingest,
    seed_runner,
    seed_session,
    seed_user,
)

_SPOOF = {"by": "mallory"}
_CALLER = "ada"

_GRAPH = """
name: alpha
entry: build
nodes:
  build:
    executor: runner
    prompt: do the work
    judgement:
      prompt: judge it
      choices:
        pass:
          description: it works
          to: done
"""


def _signed_in(tmp_path: Path) -> HubHarness:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username=_CALLER, role=Role.ADMIN)
    hub.client.cookies.set("bz_session", seed_session(hub, user))
    return hub


def _actors(hub: HubHarness, table: str, column: str = "set_by") -> list[str]:
    with hub.engine.connect() as conn:
        return [row[0] for row in conn.exec_driver_sql(f"SELECT {column} FROM {table} ORDER BY rowid")]


def _accepted(resp: Any) -> None:
    assert resp.status_code in (200, 201, 202), resp.text


def _chunk(hub: HubHarness, ref: str) -> str:
    return ingest(hub, [{"source": "default", "ref": ref}])


def test_the_chunk_routes_record_the_caller(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    paused = _chunk(hub, "1")
    stopped = _chunk(hub, "2")
    completed = _chunk(hub, "3")
    restarted = _chunk(hub, "4")
    deleted = _chunk(hub, "5")

    _accepted(hub.client.post(f"/api/chunks/{paused}/pause", json=_SPOOF))
    _accepted(hub.client.post(f"/api/chunks/{paused}/resume", json=_SPOOF))
    _accepted(hub.client.post(f"/api/chunks/{stopped}/stop", json=_SPOOF))
    _accepted(hub.client.post(f"/api/chunks/{completed}/complete", json=_SPOOF))
    _accepted(hub.client.post(f"/api/chunks/{restarted}/restart", json=_SPOOF))
    since = hub.events.latest_id()
    _accepted(hub.client.request("DELETE", f"/api/chunks/{deleted}", json=_SPOOF))

    assert _actors(hub, "chunk_pause_facts") == [_CALLER, _CALLER]
    assert _actors(hub, "chunk_stopped", "stopped_by") == [_CALLER]
    assert _actors(hub, "chunk_completed", "completed_by") == [_CALLER]
    assert _actors(hub, "chunk_restarts", "restarted_by") == [_CALLER]
    assert _actors(hub, "chunk_deleted", "deleted_by") == [_CALLER]
    [frame] = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == "chunk-changed"]
    assert frame["by"] == _CALLER


def test_the_dependency_routes_record_the_caller(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    dependent = ingest(hub, [{"source": "default", "ref": "1"}], promote=False)
    prerequisite = ingest(hub, [{"source": "default", "ref": "2"}], promote=False)
    body = {"prerequisite_chunk_id": prerequisite, **_SPOOF}

    _accepted(hub.client.post(f"/api/chunks/{dependent}/dependencies", json=body))
    _accepted(hub.client.post(f"/api/chunks/{dependent}/dependencies/release", json=body))

    assert _actors(hub, "chunk_dependencies", "declared_by") == [_CALLER]
    assert _actors(hub, "chunk_dependencies", "released_by") == [_CALLER]


def test_the_runner_routes_record_the_caller(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    seed_runner(hub, "rn_a")

    _accepted(hub.client.post("/api/runners/rn_a/pause", json=_SPOOF))
    _accepted(hub.client.post("/api/runners/rn_a/resume", json=_SPOOF))
    _accepted(hub.client.post("/api/runners/rn_a/token-revocations", json=_SPOOF))
    _accepted(hub.client.post("/api/runners/rn_a/retire", json=_SPOOF))
    _accepted(hub.client.post("/api/runners/rn_a/reinstate", json=_SPOOF))

    assert _actors(hub, "runner_pause_facts") == [_CALLER, _CALLER]
    assert _actors(hub, "runner_token_revocations", "revoked_by") == [_CALLER]
    assert _actors(hub, "runner_lifecycle_facts") == [_CALLER, _CALLER]


def test_the_host_routes_record_the_caller(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    seed_runner(hub, "rn_a")
    added = hub.client.post("/api/hosts", json={"name": "box"})
    _accepted(added)
    host_id = added.json()["host_id"]
    runners = {"runner_ids": ["rn_a"], **_SPOOF}
    since = hub.events.latest_id()

    _accepted(hub.client.post(f"/api/hosts/{host_id}/adoptions", json=runners))
    _accepted(hub.client.post(f"/api/hosts/{host_id}/releases", json=runners))
    _accepted(hub.client.post(f"/api/hosts/{host_id}/token-revocations", json=_SPOOF))
    _accepted(hub.client.post(f"/api/hosts/{host_id}/retire", json=_SPOOF))

    frames = [json.loads(e["data"]) for e in emitted_events(hub, since=since) if e["event"] == "host-changed"]
    assert {f["kind"]: f["by"] for f in frames if f["kind"] in ("adopted", "released")} == {
        "adopted": _CALLER,
        "released": _CALLER,
    }
    assert _actors(hub, "host_token_revocations", "revoked_by") == [_CALLER]
    assert _actors(hub, "host_lifecycle_facts") == [_CALLER]


def test_the_cadence_routes_record_the_caller(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    created = hub.client.post(
        f"/api/projects/{default_project(hub.engine)}/cadences", json={"slug": "evening", "spacing": "10d"}
    )
    _accepted(created)
    url = f"/api/cadences/{created.json()['cadence_id']}"

    _accepted(hub.client.post(f"{url}/retire", json=_SPOOF))
    _accepted(hub.client.post(f"{url}/enable", json=_SPOOF))

    assert _actors(hub, "cadence_lifecycle_facts") == [_CALLER, _CALLER]


def test_the_routine_routes_record_the_caller(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    _accepted(hub.client.post("/api/graphs", json={"definition_yaml": _GRAPH}))
    created = hub.client.post(
        "/api/routines", json={"name": "nightly", "graph_name": "alpha", "default_scope_slug": "blizzard"}
    )
    _accepted(created)
    url = f"/api/routines/{created.json()['routine_id']}"

    _accepted(hub.client.post(f"{url}/retire", json=_SPOOF))
    _accepted(hub.client.post(f"{url}/enable", json=_SPOOF))

    assert _actors(hub, "routine_lifecycle_facts") == [_CALLER, _CALLER]


def test_the_scope_routes_record_the_caller_on_both_paths(tmp_path: Path) -> None:
    hub = _signed_in(tmp_path)
    project = default_project(hub.engine)
    _accepted(hub.client.post("/api/scopes", json={"slug": "blizzard", "description": ""}))

    _accepted(hub.client.post("/api/scopes/blizzard/retire", json=_SPOOF))
    _accepted(hub.client.post("/api/scopes/blizzard/enable", json=_SPOOF))
    _accepted(hub.client.post(f"/api/projects/{project}/scopes/blizzard/retire", json=_SPOOF))
    _accepted(hub.client.post(f"/api/projects/{project}/scopes/blizzard/enable", json=_SPOOF))

    assert _actors(hub, "scope_lifecycle_facts") == [_CALLER] * 4


def test_a_runner_a_host_retires_under_itself_is_retired_by_that_host(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    added = hub.client.post("/api/hosts", json={"name": "box"}).json()
    headers = {"Authorization": f"Bearer {added['token']}"}
    runner_id = hub.client.post("/api/fleet/host/runners", json={"name": "mine"}, headers=headers).json()["runner_id"]

    retired = hub.client.post(f"/api/fleet/host/runners/{runner_id}/retire", json=_SPOOF, headers=headers)

    assert retired.status_code == 200, retired.text
    assert retired.json()["runner"]["retired_by"] == "box"
    assert _actors(hub, "runner_lifecycle_facts") == ["box"]
