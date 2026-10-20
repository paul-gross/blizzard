"""The version handshake at the hub (component tier), a hub at ``0.3`` under ``floor = "0.2"``."""

from __future__ import annotations

from pathlib import Path

import httpx
import pytest

from blizzard.wire.version_window import VersionWindow
from tests.support import HubHarness, build_hub, seed_runner

pytestmark = pytest.mark.component

_WINDOW = VersionWindow(floor="0.2")

#: Satisfies a chunk with no declared harness preference, so an admitted runner's claim is won.
_CAPABILITIES = [{"harness_id": "claude", "default": True}]


def _hub(tmp_path: Path) -> HubHarness:
    return build_hub(tmp_path, hub_version="0.3", version_window=_WINDOW)


def _register(hub: HubHarness, runner_id: str, version: str | None) -> httpx.Response:
    body: dict[str, object] = {"runner_id": runner_id, "workspace_id": "w1", "capabilities": _CAPABILITIES}
    if version is not None:
        body["version"] = version
    return hub.client.post("/api/fleet/runners", json=body)


def _standings(hub: HubHarness) -> dict[str, tuple[str | None, str | None, str | None]]:
    runners = hub.client.get("/api/runners").json()["runners"]
    return {r["runner_id"]: (r["version"], r["version_standing"], r["version_refusal"]) for r in runners}


def test_a_runner_below_the_floor_is_refused_too_old_naming_both_versions_and_the_floor(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    resp = _register(hub, "r1", "0.1.4")

    assert resp.status_code == 409, resp.text
    body = resp.json()
    assert body["reason"] == "too-old"
    assert body["runner_version"] == "0.1.4"
    assert body["hub_version"] == "0.3"
    assert body["floor"] == "0.2"
    assert all(named in body["detail"] for named in ("0.1.4", "0.3", "0.2"))


def test_a_runner_newer_than_the_hub_is_refused_too_new(tmp_path: Path) -> None:
    hub = _hub(tmp_path)

    resp = _register(hub, "r1", "0.4.0")

    assert resp.status_code == 409, resp.text
    assert resp.json()["reason"] == "too-new"


@pytest.mark.parametrize("version", ["0.2.1", None])
def test_a_runner_in_the_window_or_reporting_none_is_admitted_and_told_the_window(
    tmp_path: Path, version: str | None
) -> None:
    hub = _hub(tmp_path)

    resp = _register(hub, "r1", version)

    assert resp.status_code == 201, resp.text
    assert resp.json()["hub_version"] == "0.3"
    assert resp.json()["window"] == {"floor": "0.2", "ceiling": "0.3"}


def test_a_refused_registration_leaves_a_never_registered_runner_never_connected(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    seed_runner(hub, "r1", register=False)

    assert _register(hub, "r1", "0.1.4").status_code == 409

    [runner] = hub.client.get("/api/runners").json()["runners"]
    assert runner["connection"] == "never_connected"
    assert runner["registered_at"] is None
    assert (runner["version"], runner["version_standing"], runner["version_refusal"]) == ("0.1.4", "refused", "too-old")


def test_the_registry_reports_each_runners_version_and_standing(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    for runner_id in ("r1", "r2", "r3", "r4"):
        seed_runner(hub, runner_id, register=False)
    seed_runner(hub, "r5", register=False)

    _register(hub, "r1", "0.1.4")
    _register(hub, "r2", "0.4.0")
    _register(hub, "r3", "0.2.1")
    _register(hub, "r4", None)

    assert _standings(hub) == {
        "r1": ("0.1.4", "refused", "too-old"),
        "r2": ("0.4.0", "refused", "too-new"),
        "r3": ("0.2.1", "in_window", None),
        "r4": (None, "unversioned", None),
        "r5": (None, None, None),
    }


def test_a_refused_runner_claims_nothing_until_a_later_registration_is_admitted(tmp_path: Path) -> None:
    hub = _hub(tmp_path)
    assert _register(hub, "r1", "0.2.1").status_code == 201
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk}/promote").status_code == 202
    claim = {"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]}

    assert _register(hub, "r1", "0.1.4").status_code == 409
    denied = hub.client.post("/api/fleet/routes", json=claim)

    assert denied.status_code == 403, denied.text
    assert denied.json()["runner_id"] == "r1"
    assert "too-old" in denied.json()["detail"]

    assert _register(hub, "r1", "0.3.0").status_code == 201
    assert hub.client.post("/api/fleet/routes", json=claim).status_code == 201
    assert _standings(hub)["r1"] == ("0.3.0", "in_window", None)
