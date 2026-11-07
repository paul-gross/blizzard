"""Runner route write refusals observed against the actual operation declarations."""

from pathlib import Path

from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.domain.execution.auth.route import ROUTE_TOKEN_ENFORCE
from blizzard.wire.release import ReleaseProblem
from blizzard.wire.route import RouteNotHeldProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import HubHarness, build_hub, ingest, seed_runner


def _observe(hub: HubHarness, response: Response, suffix: str, status: int, model: type[BaseModel]) -> None:
    assert response.status_code == status, response.text
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["instance"] == response.request.url.path
    assert hub.app is not None
    observation = RefusalObservation(
        "post",
        f"/api/fleet/chunks/{{chunk_id}}/{suffix}",
        status,
        response.headers["content-type"],
        model,
        response.json(),
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []


def test_release_refusals_leave_the_claim_unchanged(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    chunk = ingest(hub, [{"source": "default", "ref": "release-observation"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    token = claim.json()["route_token"]
    before = hub.client.get(f"/api/chunks/{chunk}").json()
    activity = hub.client.get("/api/activity").json()
    path = f"/api/fleet/chunks/{chunk}/release"

    stale = hub.client.post(path, json={"runner_id": "r1", "epoch": 0, "reason": "throttled", "route_token": token})
    assert stale.json()["reason"] == "stale"
    assert stale.json()["chunk_id"] == chunk
    _observe(hub, stale, "release", 409, ReleaseProblem)

    other = hub.client.post(path, json={"runner_id": "r2", "epoch": 1, "reason": "throttled", "route_token": token})
    _observe(hub, other, "release", 403, RouteNotHeldProblem)
    assert hub.client.get(f"/api/chunks/{chunk}").json() == before
    assert hub.client.get("/api/activity").json() == activity


def test_release_at_a_future_epoch_is_stale_and_leaves_the_route_held(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, route_token_mode=ROUTE_TOKEN_ENFORCE)
    seed_runner(hub, "r1")
    chunk = ingest(hub, [{"source": "default", "ref": "future-release-observation"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    token = claim.json()["route_token"]
    before = hub.client.get(f"/api/chunks/{chunk}").json()
    activity = hub.client.get("/api/activity").json()

    response = hub.client.post(
        f"/api/fleet/chunks/{chunk}/release",
        json={"runner_id": "r1", "epoch": 2, "reason": "throttled", "route_token": token},
    )

    _observe(hub, response, "release", 409, ReleaseProblem)
    assert response.json()["reason"] == "stale"
    assert response.json()["chunk_id"] == chunk
    assert hub.client.get(f"/api/chunks/{chunk}").json() == before
    assert hub.client.get("/api/activity").json() == activity
    accepted = hub.client.post(
        f"/api/fleet/chunks/{chunk}/release",
        json={"runner_id": "r1", "epoch": 1, "reason": "throttled", "route_token": token},
    )
    assert accepted.status_code == 200, accepted.text


def test_other_runner_cannot_rekey_a_live_route(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    holder = seed_runner(hub, "r1")
    other = seed_runner(hub, "r2")
    chunk = ingest(hub, [{"source": "default", "ref": "rekey-observation"}])
    claim = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []},
        headers=bearer(holder),
    )
    assert claim.status_code == 201, claim.text
    before = hub.client.get(f"/api/chunks/{chunk}").json()
    activity = hub.client.get("/api/activity").json()

    response = hub.client.post(f"/api/fleet/chunks/{chunk}/route-token", headers=bearer(other))

    assert response.json()["detail"] == "token belongs to runner 'r2', not 'r1'"
    _observe(hub, response, "route-token", 403, RouteNotHeldProblem)
    assert hub.client.get(f"/api/chunks/{chunk}").json() == before
    assert hub.client.get("/api/activity").json() == activity
