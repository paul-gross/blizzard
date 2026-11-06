"""Claim refusals observed against the running app's OpenAPI declaration."""

from pathlib import Path
from typing import cast

import pytest
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from blizzard.hub.domain.runners.version_window import VersionWindow
from blizzard.wire.route import ClaimRunnerRetiredProblem, ClaimRunnerVersionRefusedProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import HubHarness, build_hub, ingest, seed_runner

_PATH = "/api/fleet/routes"


def _claim(hub: HubHarness, chunk: str) -> Response:
    return hub.client.post(
        _PATH, json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )


def _observe(hub: HubHarness, response: Response, chunk: str, model: type[BaseModel]) -> None:
    assert response.status_code == 403, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["chunk_id"] == chunk
    assert response.json()["runner_id"] == "r1"
    assert hub.app is not None
    observation = RefusalObservation(
        "post", _PATH, response.status_code, response.headers["content-type"], model, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    view = hub.client.get(f"/api/chunks/{chunk}").json()
    assert view["status"] == "ready"
    assert view["route"] is None


def test_retired_runner_claim_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "r1")
    chunk = ingest(hub, [{"source": "default", "ref": "retired"}])
    # A retirement racing token revocation can still reach claim admission.
    writer = cast(IWriteRunnerRegistry, hub.services.registry)
    writer.record_lifecycle("r1", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0)

    response = _claim(hub, chunk)

    assert "retired" in response.json()["detail"]
    _observe(hub, response, chunk, ClaimRunnerRetiredProblem)


@pytest.mark.parametrize(("version", "reason"), [("0.1.4", "too-old"), ("0.4.0", "too-new")])
def test_version_refused_runner_claim_matches_openapi(tmp_path: Path, version: str, reason: str) -> None:
    hub = build_hub(tmp_path, hub_version="0.3", version_window=VersionWindow(floor="0.2"))
    body = {"runner_id": "r1", "workspace_id": "w1", "capabilities": [{"harness_id": "claude", "default": True}]}
    assert hub.client.post("/api/fleet/runners", json={**body, "version": "0.2.1"}).status_code == 201
    chunk = ingest(hub, [{"source": "default", "ref": "version"}])
    assert hub.client.post("/api/fleet/runners", json={**body, "version": version}).status_code == 409

    response = _claim(hub, chunk)

    assert reason in response.json()["detail"]
    _observe(hub, response, chunk, ClaimRunnerVersionRefusedProblem)
    assert hub.client.post("/api/fleet/runners", json={**body, "version": "0.3.0"}).status_code == 201
    assert _claim(hub, chunk).status_code == 201
