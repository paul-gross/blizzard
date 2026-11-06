"""Observe shared refusals on additional runner-reached fleet reads."""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
)
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import RunnerRetiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem
from tests.support.credential_runners import seed_credential_runners
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches
from tests.support.scaffolding import build_hub

OPERATIONS = [
    ("GET", "/api/fleet/system-artifacts/{name}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/findings"),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals"),
    ("GET", "/api/fleet/chunks/{chunk_id}/garden/proposals/page"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/files"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/skills"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/agent-types"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/counts/nodes"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/nodes"),
    ("GET", "/api/fleet/chunks/{chunk_id}/analytics/spend/graphs"),
    ("GET", "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"),
    ("GET", "/api/fleet/chunks/{chunk_id}/transcript-segments"),
    ("GET", "/api/fleet/runners/{runner_id}/decisions"),
]


@pytest.mark.parametrize(("method", "path"), OPERATIONS)
def test_shared_refusals_on_runner_reads(tmp_path: Path, method: str, path: str) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    client = TestClient(hub.app)
    live, revoked, retired = seed_credential_runners(hub)
    other = hub.add_tenant("other")
    url = path.format(name="missing", chunk_id="ch_missing", finding_id="missing", runner_id="live")
    cases = [
        (dict[str, str](), 401, AuthenticationRequiredProblem),
        (bearer("unknown-token"), 401, AuthenticationUnresolvedProblem),
        (bearer(revoked), 401, AuthenticationRevokedProblem),
        (bearer(retired), 403, RunnerRetiredProblem),
        ({**bearer(live), "X-Blizzard-Tenant": other.tenant}, 400, TenantCredentialMismatchProblem),
        ({**bearer(live), "X-Blizzard-Tenant": "ten_missing"}, 400, TenantCredentialMismatchProblem),
    ]
    for headers, status, model in cases:
        response = client.request(method, url, headers=headers, params={"since": "2025-01-01T00:00:00Z"})
        assert response.status_code == status, response.text
        observation = RefusalObservation(method, path, status, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []

    hub.app.state.services = None
    response = client.request(method, url, headers=bearer(live))
    assert response.status_code == 503, response.text
    observation = RefusalObservation(
        method, path, 503, response.headers["content-type"], HubStoreUnavailableProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
