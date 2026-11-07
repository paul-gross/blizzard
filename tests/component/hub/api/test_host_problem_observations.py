"""Host lifecycle refusals observed against the generated API contract."""

from collections.abc import Iterable
from pathlib import Path
from typing import cast

import pytest
from httpx2 import Response
from pydantic import BaseModel

from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerRegistration
from blizzard.wire.host import HostNameTakenProblem
from blizzard.wire.runner import RunnerAlreadyHostedProblem, RunnerLifecycleConflictProblem, RunnerRetiredProblem
from blizzard.wire.tenant import TenantRequiredProblem
from tests.support.refusal_observation import RefusalObservation, refusal_mismatches, unobserved_variants
from tests.support.scaffolding import HubHarness, build_hub, default_tenant, seed_runner


def _observe(hub: HubHarness, response: Response, model: type[BaseModel]) -> None:
    assert hub.app is not None
    assert response.status_code == 409, response.text
    observation = RefusalObservation(
        "post", "/api/hosts", 409, response.headers["content-type"], model, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert response.json()["instance"] == "/api/hosts"


def test_host_add_lifecycle_refusals_match_openapi(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    host = hub.client.post("/api/hosts", json={"name": "box"})
    assert host.status_code == 201, host.text
    seed_runner(hub, "hosted")
    seed_runner(hub, "retired")
    seed_runner(hub, "racer")
    adopted = hub.client.post(f"/api/hosts/{host.json()['host_id']}/adoptions", json={"runner_ids": ["hosted"]})
    assert adopted.status_code == 200, adopted.text
    retired = hub.client.post("/api/runners/retired/retire", json={})
    assert retired.status_code == 200, retired.text

    cases = (
        (hub.client.post("/api/hosts", json={"name": "box"}), HostNameTakenProblem),
        (hub.client.post("/api/hosts", json={"name": "other", "adopt": ["hosted"]}), RunnerAlreadyHostedProblem),
        (hub.client.post("/api/hosts", json={"name": "other", "adopt": ["retired"]}), RunnerRetiredProblem),
    )
    observed: set[type[BaseModel]] = set()
    for response, model in cases:
        _observe(hub, response, model)
        observed.add(model)

    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    read = registry.get_runners

    def retire_after_read(runner_ids: Iterable[str]) -> dict[str, RunnerRegistration]:
        result = read(runner_ids)
        registry.record_lifecycle("racer", retired=True, at=hub.clock.now(), by="racer", expected_lifecycle=0)
        return result

    monkeypatch.setattr(registry, "get_runners", retire_after_read)
    response = hub.client.post("/api/hosts", json={"name": "other", "adopt": ["racer"]})
    _observe(hub, response, RunnerLifecycleConflictProblem)
    observed.add(RunnerLifecycleConflictProblem)
    assert [item["host_name"] for item in hub.client.get("/api/hosts").json()["hosts"]] == ["box"]

    second = hub.add_tenant("second")
    response = hub.client.post("/api/hosts", json={"name": "ambiguous"})
    _observe(hub, response, TenantRequiredProblem)
    assert response.json()["detail"] == {
        "code": "tenant_required",
        "message": "name the tenant to act in with X-Blizzard-Tenant",
    }
    observed.add(TenantRequiredProblem)
    assert (
        unobserved_variants(hub.app.openapi(), "post", "/api/hosts", 409, "application/problem+json", observed) == set()
    )
    headers = {"X-Blizzard-Tenant": second.tenant}
    assert hub.client.get("/api/hosts", headers=headers).json()["hosts"] == []
    first_headers = {"X-Blizzard-Tenant": default_tenant(hub.engine)}
    assert [item["host_name"] for item in hub.client.get("/api/hosts", headers=first_headers).json()["hosts"]] == [
        "box"
    ]
    created = hub.client.post("/api/hosts", json={"name": "ambiguous"}, headers=headers)
    assert created.status_code == 201, created.text


def test_host_bootstrap_rejects_machine_credentials_before_writing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    runner_token = seed_runner(hub, "runner")
    host = hub.client.post("/api/hosts", json={"name": "existing"}).json()
    cases = (
        (
            runner_token,
            "foreign",
            403,
            "runner-credential-forbidden",
            "runner token for 'runner' is not valid on an operator verb",
        ),
        (
            host["token"],
            "foreign",
            400,
            "tenant-credential-mismatch",
            "X-Blizzard-Tenant names a tenant other than the presented credential's",
        ),
        (host["token"], None, 403, "machine-credential-forbidden", "a machine credential is not valid here"),
    )
    for token, tenant, status, kind, detail in cases:
        headers = {"Authorization": f"Bearer {token}"}
        if tenant:
            headers["X-Blizzard-Tenant"] = tenant
        response = hub.client.post("/api/hosts", headers=headers, json={"name": "new"})
        assert response.status_code == status
        assert response.headers["content-type"] == "application/problem+json"
        assert response.json()["type"] == f"urn:blizzard:problem:{kind}"
        assert response.json()["detail"] == detail
        assert response.json()["instance"] == "/api/hosts"
    assert hub.client.post("/api/hosts", json={"name": "new"}).status_code == 201
