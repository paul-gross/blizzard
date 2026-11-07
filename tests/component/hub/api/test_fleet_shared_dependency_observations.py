"""Observe shared fleet authentication and tenant refusals per operation."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.tenant import TenantCredentialMismatchProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub, seed_runner

OPERATIONS = [
    ("GET", "/api/fleet/chunk-statuses"),
    ("GET", "/api/fleet/chunks/{chunk_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/envelope"),
    ("GET", "/api/fleet/chunks/{chunk_id}/work-items"),
    ("GET", "/api/fleet/chunks/{chunk_id}/pm-items"),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings"),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"),
    *(
        ("GET", f"/api/fleet/chunks/{{chunk_id}}/{suffix}")
        for suffix in (
            "garden/findings",
            "garden/proposals",
            "garden/proposals/page",
            "analytics/counts/files",
            "analytics/counts/skills",
            "analytics/counts/agent-types",
            "analytics/counts/nodes",
            "analytics/spend/nodes",
            "analytics/spend/graphs",
        )
    ),
    ("GET", "/api/fleet/questions/{question_id}"),
    ("GET", "/api/fleet/runners/{runner_id}"),
    ("GET", "/api/fleet/runners/{runner_id}/decisions"),
    ("GET", "/api/fleet/scopes"),
    ("GET", "/api/fleet/summary"),
    ("GET", "/api/fleet/system-artifacts"),
    ("GET", "/api/fleet/system-artifacts/{name}"),
    ("GET", "/api/fleet/queue/peek"),
    ("POST", "/api/fleet/queue/peek"),
    ("POST", "/api/fleet/routes"),
    ("POST", "/api/fleet/events"),
    ("POST", "/api/fleet/transcripts"),
    ("POST", "/api/fleet/runners"),
    ("POST", "/api/fleet/chunks/{chunk_id}/completions"),
    ("POST", "/api/fleet/chunks/{chunk_id}/decisions"),
    ("POST", "/api/fleet/chunks/{chunk_id}/hub-advance"),
    ("POST", "/api/fleet/chunks/{chunk_id}/pause"),
    ("POST", "/api/fleet/chunks/{chunk_id}/release"),
    ("POST", "/api/fleet/chunks/{chunk_id}/resume"),
    ("POST", "/api/fleet/chunks/{chunk_id}/route-token"),
]


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_shared_refusals_match_each_operation(tmp_path: Path, method: str, path: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    token = seed_runner(hub, "runner-a")
    revoked = hub.client.post("/api/runners/runner-a/token-revocations", json={"by": "operator"})
    assert revoked.status_code == 201, revoked.text
    live_token = seed_runner(hub, "runner-b")
    other = hub.add_tenant("other")
    url = path.format(
        chunk_id="ch_missing", question_id="q_missing", runner_id="runner-a", finding_id="f_missing", name="missing"
    )
    cases = [
        (dict[str, str](), 401, AuthenticationRequiredProblem, "missing or malformed Authorization header"),
        (
            bearer("not-a-real-token"),
            401,
            AuthenticationUnresolvedProblem,
            "bearer token does not resolve to a known runner",
        ),
        ({**bearer(live_token), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem, None),
    ]
    for headers, status, model, detail in cases:
        response = client.request(method, url, headers=headers, json={})
        assert response.status_code == status, response.text
        if detail is not None:
            assert response.json()["detail"] == detail
        observation = RefusalObservation(method, path, status, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []

    response = client.request(method, url, headers=bearer(token), json={})
    assert response.status_code == 401, response.text
    assert response.json()["detail"] == "bearer token has been revoked"
    observation = RefusalObservation(
        method, path, 401, response.headers["content-type"], AuthenticationRevokedProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
