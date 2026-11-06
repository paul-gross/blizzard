"""Observed hub refusals agree with the generated operation declarations."""

from copy import deepcopy
from pathlib import Path

import pytest
from fastapi import Request
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.hub.api.auth import RunnerAuth
from blizzard.hub.api.auth_session import TenantRequest, resolve_tenant
from blizzard.hub.api.hub_level import Hub
from blizzard.hub.domain.chunk.dependencies import PrerequisiteScope
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    CliPkceRequiredProblem,
    CliRedirectRefusedProblem,
    CliTokenRefusedProblem,
    FederationClientRefusedProblem,
    FederationResponseModeProblem,
    IdpDisabledProblem,
)
from blizzard.wire.chunk import ChunkNotFoundProblem, NoAnsweredProposalProblem, NoRunContextProblem
from blizzard.wire.host import HostNameTakenProblem
from blizzard.wire.question import QuestionNotFoundProblem
from blizzard.wire.route import (
    ClaimRunnerUnregisteredProblem,
    RouteClaimConflictProblem,
    RouteClaimDependencyProblem,
    RouteClaimIncompatibleProblem,
    RouteClaimNotReadyProblem,
    RouteClaimPausedProblem,
    RouteClaimTerminalProblem,
    RouteNotFoundProblem,
)
from blizzard.wire.runner import (
    RunnerIdentityProblem,
    RunnerNotFoundProblem,
    RunnerNotRegisteredProblem,
    RunnerOwnershipProblem,
    UnknownServedProjectProblem,
)
from blizzard.wire.system_artifact import SystemArtifactNotFoundProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import (
    RefusalObservation,
    coverage_mismatches,
    refusal_mismatches,
    unobserved_variants,
)
from tests.support.scaffolding import HubHarness, build_hub, close_tenant, ingest, seed_runner, seed_session, seed_user


@pytest.mark.parametrize(
    "suffix",
    [
        "garden/findings",
        "garden/proposals",
        "garden/proposals/page",
        "analytics/counts/files",
        "analytics/counts/skills",
        "analytics/counts/agent-types",
        "analytics/counts/nodes",
        "analytics/spend/nodes",
        "analytics/spend/graphs",
        "findings",
        "findings/fin_missing",
    ],
)
def test_runner_read_tenant_closes_between_authentication_and_resolution(
    tmp_path: Path, suffix: str, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    path = f"/api/fleet/chunks/ch_missing/{suffix}"
    template = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
    if suffix == "findings/fin_missing":
        template = "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"

    def close_before_resolution(request: Request, services: Hub) -> TenantRequest:
        principal = RunnerAuth.of(request, services).demand()
        close_tenant(hub.engine, principal.tenant_id)
        services.tenants.open_tenants.refresh()
        return resolve_tenant(request, services)

    monkeypatch.setitem(hub.app.dependency_overrides, resolve_tenant, close_before_resolution)
    response = hub.client.get(path)
    assert response.status_code == 404, response.text
    _assert_declared(hub, response, "get", template, TenantNotFoundProblem)


def _assert_declared(hub: HubHarness, response: Response, method: str, path: str, model: type[BaseModel]) -> None:
    assert hub.app is not None
    observation = RefusalObservation(
        method, path, response.status_code, response.headers["content-type"], model, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []


def _assert_only_observed_variant(
    hub: HubHarness, response: Response, method: str, path: str, model: type[BaseModel]
) -> None:
    _assert_declared(hub, response, method, path, model)
    assert hub.app is not None
    media_type = response.headers["content-type"].split(";", 1)[0].strip().lower()
    assert unobserved_variants(hub.app.openapi(), method, path, response.status_code, media_type, {model}) == set()


class ValidationErrorItem(BaseModel):
    loc: list[str | int]
    msg: str
    type: str


class HTTPValidationError(BaseModel):
    detail: list[ValidationErrorItem]


def test_claim_incompatibility_matches_its_openapi_variant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "r1", projects=())
    created = hub.client.post("/api/chunks", json={"tokens": ["default:1"]})
    assert created.status_code == 201, created.text
    chunk_id = created.json()["chunk_id"]
    assert hub.client.post(f"/api/chunks/{chunk_id}/promote").status_code == 202

    response = hub.client.post(
        "/api/fleet/routes",
        json={"chunk_id": chunk_id, "runner_id": "r1", "workspace_id": "w1", "environment_ids": ["env-a"]},
    )

    assert response.status_code == 409, response.text
    assert response.json()["reason"] == "project"
    _assert_declared(hub, response, "post", "/api/fleet/routes", RouteClaimIncompatibleProblem)


def test_claim_missing_chunk_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "r1")
    path = "/api/fleet/routes"
    response = hub.client.post(
        path, json={"chunk_id": "ch_missing", "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert response.status_code == 404, response.text
    _assert_declared(hub, response, "post", path, ChunkNotFoundProblem)


def test_bootstrap_identity_refusal_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    response = TestClient(hub.app).get("/api/fleet/identity")

    assert response.status_code == 401
    assert response.json()["reason"] == "missing"
    _assert_only_observed_variant(hub, response, "get", "/api/fleet/identity", RunnerIdentityProblem)


def test_tenant_refusals_are_observed_through_real_requests(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    path = "/api/hosts"
    unknown = hub.client.post(path, json={"name": "box"}, headers={"X-Blizzard-Tenant": "ten_missing"})
    assert unknown.status_code == 404, unknown.text
    assert unknown.json()["detail"] == "no such tenant"
    _assert_declared(hub, unknown, "post", path, TenantNotFoundProblem)

    host = hub.client.post(path, json={"name": "box"})
    assert host.status_code == 201, host.text
    other = hub.add_tenant("other")
    fleet_path = "/api/fleet/host"
    mismatch = hub.client.get(fleet_path, headers={**bearer(host.json()["token"]), "X-Blizzard-Tenant": other.tenant})
    assert mismatch.status_code == 400, mismatch.text
    _assert_declared(hub, mismatch, "get", fleet_path, TenantCredentialMismatchProblem)


def test_missing_session_refusal_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    response = hub.client.get("/api/me")

    assert response.status_code == 401
    _assert_only_observed_variant(hub, response, "get", "/api/me", AuthenticationRequiredProblem)


def test_independent_missing_resources_and_bootstrap_refusals(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"})
    assert host.status_code == 201
    duplicate = hub.client.post("/api/hosts", json={"name": "box"})
    assert duplicate.status_code == 409
    _assert_declared(hub, duplicate, "post", "/api/hosts", HostNameTakenProblem)

    missing_runner = hub.client.post(
        "/api/fleet/host/runners/rn_missing/retire", json={}, headers=bearer(host.json()["token"])
    )
    assert missing_runner.status_code == 404
    _assert_declared(hub, missing_runner, "post", "/api/fleet/host/runners/{runner_id}/retire", RunnerNotFoundProblem)

    for url, template, model in (
        (
            "/api/fleet/chunks/ch_missing/garden/proposals/page",
            "/api/fleet/chunks/{chunk_id}/garden/proposals/page",
            ChunkNotFoundProblem,
        ),
        (
            "/api/fleet/system-artifacts/missing/ghost?secret=hidden",
            "/api/fleet/system-artifacts/{name}",
            SystemArtifactNotFoundProblem,
        ),
        ("/api/auth/jwks.json", "/api/auth/jwks.json", IdpDisabledProblem),
    ):
        response = hub.client.get(url)
        assert response.status_code == 404, response.text
        assert "secret" not in response.json()["instance"]
        if model is IdpDisabledProblem:
            _assert_only_observed_variant(hub, response, "get", template, model)
        else:
            _assert_declared(hub, response, "get", template, model)

    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    response = hub.client.get(f"/api/fleet/chunks/{chunk}/garden/proposals/page")
    assert response.status_code == 404
    _assert_declared(hub, response, "get", "/api/fleet/chunks/{chunk_id}/garden/proposals/page", NoRunContextProblem)


def test_missing_resource_variants_on_runner_read_paths(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    for url, template, model in (
        ("/api/fleet/chunks/ch_missing", "/api/fleet/chunks/{chunk_id}", ChunkNotFoundProblem),
        ("/api/fleet/chunks/ch_missing/envelope", "/api/fleet/chunks/{chunk_id}/envelope", ChunkNotFoundProblem),
        ("/api/fleet/chunks/ch_missing/work-items", "/api/fleet/chunks/{chunk_id}/work-items", ChunkNotFoundProblem),
        ("/api/fleet/questions/q_missing", "/api/fleet/questions/{question_id}", QuestionNotFoundProblem),
    ):
        response = hub.client.get(url)
        assert response.status_code == 404, response.text
        _assert_declared(hub, response, "get", template, model)

    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    response = hub.client.get(f"/api/fleet/chunks/{chunk}/findings")
    assert response.status_code == 404, response.text
    _assert_declared(hub, response, "get", "/api/fleet/chunks/{chunk_id}/findings", NoAnsweredProposalProblem)

    response = hub.client.post(f"/api/fleet/chunks/{chunk}/route-token", json={"runner_id": "r1"})
    assert response.status_code == 404, response.text
    _assert_declared(hub, response, "post", "/api/fleet/chunks/{chunk_id}/route-token", RouteNotFoundProblem)


def test_routine_read_404_variants_are_all_observed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    suffixes = (
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
    assert hub.app is not None
    for suffix in suffixes:
        template = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
        observed: set[type[BaseModel]] = set()
        for chunk_id, model in (("ch_missing", ChunkNotFoundProblem), (chunk, NoRunContextProblem)):
            response = hub.client.get(
                f"/api/fleet/chunks/{chunk_id}/{suffix}", params={"since": "2025-01-01T00:00:00Z"}
            )
            assert response.status_code == 404, response.text
            _assert_declared(hub, response, "get", template, model)
            observed.add(model)
        unknown = hub.client.get(
            f"/api/fleet/chunks/{chunk}/{suffix}",
            params={"since": "2025-01-01T00:00:00Z"},
            headers={"X-Blizzard-Tenant": "ten_missing"},
        )
        assert unknown.status_code == 400, unknown.text
        _assert_declared(hub, unknown, "get", template, TenantCredentialMismatchProblem)
        assert unobserved_variants(hub.app.openapi(), "get", template, 404, "application/problem+json", observed) == {
            "TenantNotFoundProblem"
        }


def test_findings_404_variants_are_all_observed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    template = "/api/fleet/chunks/{chunk_id}/findings"
    observed: set[type[BaseModel]] = set()
    for chunk_id, model in (("ch_missing", ChunkNotFoundProblem), (chunk, NoAnsweredProposalProblem)):
        response = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings")
        assert response.status_code == 404, response.text
        _assert_declared(hub, response, "get", template, model)
        observed.add(model)
    unknown = hub.client.get(f"/api/fleet/chunks/{chunk}/findings", headers={"X-Blizzard-Tenant": "ten_missing"})
    assert unknown.status_code == 400, unknown.text
    _assert_declared(hub, unknown, "get", template, TenantCredentialMismatchProblem)
    assert hub.app is not None
    assert unobserved_variants(hub.app.openapi(), "get", template, 404, "application/problem+json", observed) == {
        "TenantNotFoundProblem"
    }


def test_claim_409_variants_are_all_observed(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    seed_runner(hub, "r3", projects=())
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    path = "/api/fleet/routes"
    observed: set[type[BaseModel]] = set()

    def claim(runner: str, chunk_id: str = chunk) -> Response:
        return hub.client.post(
            path, json={"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": []}
        )

    def observe(response: Response, model: type[BaseModel]) -> None:
        assert response.status_code == 409, response.text
        _assert_declared(hub, response, "post", path, model)
        observed.add(model)

    not_ready = claim("r1")
    assert not_ready.status_code == 409
    assert not_ready.json()["status"] == "not_ready"
    observe(not_ready, RouteClaimNotReadyProblem)
    assert hub.client.post(f"/api/chunks/{chunk}/promote").status_code == 202
    assert claim("r1").status_code == 201
    conflict = claim("r2")
    assert conflict.status_code == 409
    assert "status" not in conflict.json()
    assert conflict.json()["held_by_runner_id"] == "r1"
    observe(conflict, RouteClaimConflictProblem)

    incompatible_chunk = ingest(hub, [{"source": "default", "ref": "incompatible"}])
    incompatible = claim("r3", incompatible_chunk)
    assert incompatible.json()["reason"] == "project"
    observe(incompatible, RouteClaimIncompatibleProblem)

    terminal_chunk = ingest(hub, [{"source": "default", "ref": "terminal"}])
    assert hub.client.post(f"/api/chunks/{terminal_chunk}/stop", json={"by": "operator"}).status_code == 202
    terminal = claim("r1", terminal_chunk)
    assert terminal.json()["status"] == "stopped"
    observe(terminal, RouteClaimTerminalProblem)

    dependent_id = ingest(hub, [{"source": "default", "ref": "dependent"}])
    prerequisite_id = ingest(hub, [{"source": "default", "ref": "prerequisite"}])
    dependent = hub.services.chunks.record.get(dependent_id)
    assert dependent is not None
    hub.services.dependencies.declare(dependent, PrerequisiteScope(prerequisite_id), by="user:alice")
    dependency = claim("r1", dependent_id)
    assert dependency.json()["prerequisite_chunk_id"] == prerequisite_id
    observe(dependency, RouteClaimDependencyProblem)

    assert hub.app is not None
    assert unobserved_variants(hub.app.openapi(), "post", path, 409, "application/problem+json", observed) == set()


def test_claim_paused_and_unregistered_variants_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auto_register_claimants=False)
    seed_runner(hub, "r1")
    chunk = ingest(hub, [{"source": "default", "ref": "claim"}])
    path = "/api/fleet/routes"
    assert hub.client.post("/api/runners/r1/pause", json={"by": "operator"}).status_code == 200

    for runner, model in (("r1", RouteClaimPausedProblem), ("r-missing", ClaimRunnerUnregisteredProblem)):
        response = hub.client.post(
            path, json={"chunk_id": chunk, "runner_id": runner, "workspace_id": "w1", "environment_ids": []}
        )
        assert response.status_code == 403, response.text
        assert response.json()["chunk_id"] == chunk
        assert response.json()["runner_id"] == runner
        _assert_declared(hub, response, "post", path, model)
    assert hub.client.get(f"/api/chunks/{chunk}").json()["status"] == "ready"


def test_runner_read_ownership_and_unregistered_variants_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    token = seed_runner(hub, "runner-a")
    seed_runner(hub, "runner-b")
    template = "/api/fleet/runners/{runner_id}"
    ownership = hub.client.get("/api/fleet/runners/runner-b", headers=bearer(token))
    assert ownership.status_code == 403, ownership.text
    assert ownership.json()["detail"] == "token belongs to runner 'runner-a', not 'runner-b'"
    _assert_declared(hub, ownership, "get", template, RunnerOwnershipProblem)

    unregistered = hub.client.get("/api/fleet/runners/ghost")
    assert unregistered.status_code == 409, unregistered.text
    assert unregistered.json()["detail"] == "runner ghost has not registered"
    _assert_declared(hub, unregistered, "get", template, RunnerNotRegisteredProblem)


def test_federation_and_cli_refusals_match_their_variants(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR, email="alice@example.com")
    hub.client.cookies.set("bz_session", seed_session(hub, user))
    path = "/api/auth/authorize"
    base = {"client": "runner-missing", "redirect_uri": "https://example.com/callback", "state": "secret-state"}
    observed: set[type[BaseModel]] = set()
    for params, model in (
        ({**base, "response_mode": "query"}, FederationResponseModeProblem),
        (base, FederationClientRefusedProblem),
        ({**base, "client": "cli"}, CliRedirectRefusedProblem),
        ({**base, "client": "cli", "redirect_uri": "http://127.0.0.1:54321/callback"}, CliPkceRequiredProblem),
    ):
        response = hub.client.get(path, params=params, follow_redirects=False)
        assert response.status_code == 400, response.text
        assert response.json()["instance"] == path
        assert "secret-state" not in response.text
        _assert_declared(hub, response, "get", path, model)
        observed.add(model)

    assert hub.app is not None
    assert unobserved_variants(hub.app.openapi(), "get", path, 400, "application/problem+json", observed) == set()

    token_path = "/api/auth/cli/token"
    refused = hub.client.post(
        token_path,
        json={"code": "invalid", "code_verifier": "invalid", "redirect_uri": "http://127.0.0.1:54321/callback"},
    )
    assert refused.status_code == 400, refused.text
    _assert_only_observed_variant(hub, refused, "post", token_path, CliTokenRefusedProblem)


def test_host_domain_and_framework_422_are_distinct(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    headers = bearer(host["token"])
    path = "/api/fleet/host/runners"
    domain = hub.client.post(path, json={"name": "runner", "projects": ["missing"]}, headers=headers)
    assert domain.status_code == 422
    assert isinstance(domain.json()["detail"], str)
    _assert_only_observed_variant(hub, domain, "post", path, UnknownServedProjectProblem)

    validation = hub.client.post(path, json={"name": []}, headers=headers)
    assert validation.status_code == 422
    assert validation.headers["content-type"] == "application/json"
    assert isinstance(validation.json()["detail"], list)
    _assert_only_observed_variant(hub, validation, "post", path, HTTPValidationError)


def test_observed_domain_refusal_rejects_planted_declaration_drift(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host = hub.client.post("/api/hosts", json={"name": "box"}).json()
    path = "/api/fleet/host/runners"
    response = hub.client.post(path, json={"name": "runner", "projects": ["missing"]}, headers=bearer(host["token"]))
    assert response.status_code == 422
    observation = RefusalObservation(
        "post", path, 422, response.headers["content-type"], UnknownServedProjectProblem, response.json()
    )
    assert hub.app is not None
    original = hub.app.openapi()
    assert refusal_mismatches(original, observation) == []

    # An observation cannot silently cover a newly declared alternative.
    selected = {("post", path)}
    missing = coverage_mismatches(original, selected, [observation])
    assert not any("UnknownServedProjectProblem" in item for item in missing)

    planted = deepcopy(original)
    content = planted["paths"][path]["post"]["responses"]["422"]["content"]
    content["application/problem+json"]["schema"] = {"$ref": "#/components/schemas/RunnerIdentityProblem"}
    assert refusal_mismatches(planted, observation) == ["missing model: UnknownServedProjectProblem"]
    assert any(
        "unobserved:" in item and "RunnerIdentityProblem" in item
        for item in coverage_mismatches(planted, selected, [observation])
    )
    del content["application/problem+json"]
    assert refusal_mismatches(planted, observation) == ["missing media type: application/problem+json"]
    del planted["paths"][path]["post"]["responses"]["422"]
    assert refusal_mismatches(planted, observation) == ["missing status: 422"]
