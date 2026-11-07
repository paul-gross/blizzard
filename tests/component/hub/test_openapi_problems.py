from copy import deepcopy
from datetime import UTC, datetime
from pathlib import Path
from typing import Any, cast

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel
from pydantic.json_schema import models_json_schema

from blizzard.auth_core import Role
from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.foundation.garden_proposals import GardenProposalOrigin
from blizzard.foundation.tokens import TokenHash
from blizzard.hub.app import create_app_for_export
from blizzard.hub.domain.chunk.dependencies import PrerequisiteScope
from blizzard.hub.domain.graph.model import Graph
from blizzard.hub.domain.runners.host import HostTokenRotation
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry
from blizzard.hub.domain.runners.version_window import VersionWindow
from blizzard.hub.store.internal.finding_store import FindingStore
from blizzard.hub.store.internal.garden_proposal_store import GardenProposalStore
from blizzard.hub.store.internal.host_registry_store import HostRegistryStore
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    AuthenticationRevokedProblem,
    AuthenticationUnresolvedProblem,
    CliPkceRequiredProblem,
    CliRedirectRefusedProblem,
    FederationClientRefusedProblem,
    FederationResponseModeProblem,
    IdpDisabledProblem,
    MachineCredentialForbiddenProblem,
    PermissionRequiredProblem,
    RunnerCredentialForbiddenProblem,
)
from blizzard.wire.chunk import (
    ChunkNotFoundProblem,
    FindingNotInChunkProblem,
    NoAnsweredProposalProblem,
    NoRunContextProblem,
)
from blizzard.wire.host import HostNameTakenProblem, HostRetiredProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.project import UnknownProjectProblem
from blizzard.wire.question import QuestionNotFoundProblem
from blizzard.wire.release import ReleaseProblem
from blizzard.wire.route import (
    ClaimRunnerRetiredProblem,
    ClaimRunnerUnregisteredProblem,
    ClaimRunnerVersionRefusedProblem,
    PinnedGraphMissingProblem,
    RouteClaimConflictProblem,
    RouteClaimDependencyProblem,
    RouteClaimIncompatibleProblem,
    RouteClaimNotReadyProblem,
    RouteClaimPausedProblem,
    RouteClaimTerminalProblem,
    RouteNotFoundProblem,
    RouteNotHeldProblem,
    RouteTerminalProblem,
)
from blizzard.wire.runner import (
    RunnerHoldsRoutesProblem,
    RunnerLifecycleConflictProblem,
    RunnerOwnershipProblem,
    RunnerRetiredProblem,
    RunnerRetireInterruptedProblem,
    RunnerVersionProblem,
    UnknownServedProjectProblem,
)
from blizzard.wire.system_artifact import SystemArtifactNotFoundProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem, TenantRequiredProblem
from tests.support.hub_request_auth import bearer
from tests.support.refusal_observation import (
    RefusalObservation,
    observe_refusal,
    refusal_mismatches,
    unobserved_variants,
)
from tests.support.scaffolding import (
    build_hub,
    default_project,
    hub_store_connections,
    ingest,
    report_lease,
    seed_runner,
    seed_scope,
    seed_session,
    seed_user,
)


def test_direct_problem_responses_and_dependencies() -> None:
    app = create_app_for_export(packaged_flag_registry())
    spec = app.openapi()
    paths = spec["paths"]
    claim = paths["/api/fleet/routes"]["post"]["responses"]
    host = paths["/api/hosts"]["post"]["responses"]
    me = paths["/api/me"]["get"]["responses"]
    media = "application/problem+json"
    heartbeat = paths["/api/fleet/runners/{runner_id}/heartbeats"]["post"]["responses"]
    assert {ref["$ref"] for ref in heartbeat["403"]["content"][media]["schema"]["anyOf"]} == {
        "#/components/schemas/RunnerOwnershipProblem",
        "#/components/schemas/RunnerRetiredProblem",
    }
    assert {ref["$ref"] for ref in heartbeat["404"]["content"][media]["schema"]["anyOf"]} == {
        "#/components/schemas/RunnerNotFoundProblem",
        "#/components/schemas/TenantNotFoundProblem",
    }
    queue = paths["/api/fleet/queue/peek"]["get"]["responses"]["422"]["content"]
    assert queue[media]["schema"] == {"$ref": "#/components/schemas/UnknownProjectProblem"}
    assert queue["application/json"]["schema"] == {"$ref": "#/components/schemas/HTTPValidationError"}
    assert {r["$ref"].split("/")[-1] for r in claim["404"]["content"][media]["schema"]["anyOf"]} == {
        "ChunkNotFoundProblem",
        "TenantNotFoundProblem",
    }
    assert claim["500"]["content"][media]["schema"] == {"$ref": "#/components/schemas/PinnedGraphMissingProblem"}
    assert {ref["$ref"].split("/")[-1] for ref in claim["409"]["content"][media]["schema"]["anyOf"]} == {
        "RouteClaimTerminalProblem",
        "RouteClaimNotReadyProblem",
        "RouteClaimDependencyProblem",
        "RouteClaimIncompatibleProblem",
        "RouteClaimConflictProblem",
    }
    assert {r["$ref"].split("/")[-1] for r in host["409"]["content"][media]["schema"]["anyOf"]} == {
        "HostNameTakenProblem",
        "RunnerAlreadyHostedProblem",
        "RunnerRetiredProblem",
        "RunnerLifecycleConflictProblem",
        "TenantRequiredProblem",
    }
    assert me["401"]["content"][media]["schema"] == {"$ref": "#/components/schemas/AuthenticationRequiredProblem"}
    assert len(claim["401"]["content"][media]["schema"]["anyOf"]) == 3
    assert host["401"]["content"][media]["schema"] == {"$ref": "#/components/schemas/AuthenticationRequiredProblem"}
    assert {r["$ref"].split("/")[-1] for r in host["403"]["content"][media]["schema"]["anyOf"]} == {
        "MachineCredentialForbiddenProblem",
        "RunnerCredentialForbiddenProblem",
        "PermissionRequiredProblem",
    }
    assert claim["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/HTTPValidationError"}
    assert "application/json" in claim["201"]["content"]
    assert app.openapi() == spec


def test_rekey_problem_variants() -> None:
    paths = create_app_for_export(packaged_flag_registry()).openapi()["paths"]
    responses = paths["/api/fleet/chunks/{chunk_id}/route-token"]["post"]["responses"]
    media = "application/problem+json"
    for status, expected in (
        ("403", {"RunnerRetiredProblem", "RouteNotHeldProblem"}),
        ("404", {"TenantNotFoundProblem", "RouteNotFoundProblem"}),
    ):
        assert {r["$ref"].split("/")[-1] for r in responses[status]["content"][media]["schema"]["anyOf"]} == expected
    assert responses["409"]["content"][media]["schema"] == {"$ref": "#/components/schemas/RouteTerminalProblem"}
    assert responses["422"]["content"]["application/json"]["schema"] == {
        "$ref": "#/components/schemas/HTTPValidationError"
    }


def test_mapped_responses_preserve_validation_and_exclude_unimplemented_variants() -> None:
    spec = create_app_for_export(packaged_flag_registry()).openapi()
    paths = spec["paths"]
    media = "application/problem+json"
    page = paths["/api/fleet/chunks/{chunk_id}/garden/proposals/page"]["get"]["responses"]
    assert page["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/HTTPValidationError"}
    assert page["422"]["content"][media]["schema"] == {"$ref": "#/components/schemas/MalformedCursorProblem"}
    assert {r["$ref"].split("/")[-1] for r in page["404"]["content"][media]["schema"]["anyOf"]} == {
        "ChunkNotFoundProblem",
        "NoRunContextProblem",
        "TenantNotFoundProblem",
    }
    artifact = paths["/api/fleet/system-artifacts/{name}"]["get"]["responses"]
    assert {r["$ref"].split("/")[-1] for r in artifact["404"]["content"][media]["schema"]["anyOf"]} == {
        "SystemArtifactNotFoundProblem",
        "TenantNotFoundProblem",
    }
    release = paths["/api/fleet/chunks/{chunk_id}/release"]["post"]["responses"]
    assert release["409"]["content"][media]["schema"] == {"$ref": "#/components/schemas/ReleaseProblem"}
    findings = paths["/api/fleet/chunks/{chunk_id}/findings/{finding_id}"]["get"]["responses"]
    assert {r["$ref"].split("/")[-1] for r in findings["404"]["content"][media]["schema"]["anyOf"]} == {
        "ChunkNotFoundProblem",
        "FindingNotInChunkProblem",
        "NoAnsweredProposalProblem",
        "TenantNotFoundProblem",
    }


def test_federation_and_retired_responses() -> None:
    spec = create_app_for_export(packaged_flag_registry()).openapi()
    paths = spec["paths"]
    media = "application/problem+json"
    authorize = paths["/api/auth/authorize"]["get"]["responses"]
    assert {r["$ref"].split("/")[-1] for r in authorize["400"]["content"][media]["schema"]["anyOf"]} == {
        "FederationResponseModeProblem",
        "FederationClientRefusedProblem",
        "CliRedirectRefusedProblem",
        "CliPkceRequiredProblem",
    }
    assert {r["$ref"].split("/")[-1] for r in authorize["403"]["content"][media]["schema"]["anyOf"]} == {
        "RunnerRetiredProblem",
        "HostRetiredProblem",
    }
    assert authorize["501"]["content"][media]["schema"] == {"$ref": "#/components/schemas/FederationNoProviderProblem"}
    for path, method in (("/api/auth/jwks.json", "get"), ("/api/auth/cli/token", "post")):
        assert paths[path][method]["responses"]["404"]["content"][media]["schema"] == {
            "$ref": "#/components/schemas/IdpDisabledProblem"
        }
        assert paths[path][method]["responses"]["503"]["content"][media]["schema"] == {
            "$ref": "#/components/schemas/HubStoreUnavailableProblem"
        }
    token = paths["/api/auth/cli/token"]["post"]["responses"]
    assert token["400"]["content"][media]["schema"] == {"$ref": "#/components/schemas/CliTokenRefusedProblem"}
    assert token["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/HTTPValidationError"}
    login = paths["/api/auth/{name}/authorize"]["get"]["responses"]
    assert {r["$ref"].split("/")[-1] for r in login["404"]["content"][media]["schema"]["anyOf"]} == {
        "LoginDisabledProblem",
        "LoginProviderUnknownProblem",
    }
    assert login["429"]["content"][media]["schema"] == {"$ref": "#/components/schemas/LoginThrottledProblem"}
    callback = paths["/api/auth/{name}/callback"]["get"]["responses"]
    assert {r["$ref"].split("/")[-1] for r in callback["400"]["content"][media]["schema"]["anyOf"]} == {
        "LoginFailedProblem",
        "SsoRefusedProblem",
    }


def test_fleet_host_and_registration_problems() -> None:
    paths = create_app_for_export(packaged_flag_registry()).openapi()["paths"]
    media = "application/problem+json"
    add = paths["/api/fleet/host/runners"]["post"]["responses"]
    assert add["422"]["content"][media]["schema"] == {"$ref": "#/components/schemas/UnknownServedProjectProblem"}
    assert add["422"]["content"]["application/json"]["schema"] == {"$ref": "#/components/schemas/HTTPValidationError"}
    assert add["403"]["content"][media]["schema"] == {"$ref": "#/components/schemas/HostRetiredProblem"}
    retire = paths["/api/fleet/host/runners/{runner_id}/retire"]["post"]["responses"]
    assert {r["$ref"].split("/")[-1] for r in retire["409"]["content"][media]["schema"]["anyOf"]} == {
        "RunnerHoldsRoutesProblem",
        "RunnerLifecycleConflictProblem",
        "RunnerRetireInterruptedProblem",
    }
    register = paths["/api/fleet/runners"]["post"]["responses"]
    assert register["409"]["content"][media]["schema"] == {"$ref": "#/components/schemas/RunnerVersionProblem"}
    assert {r["$ref"].split("/")[-1] for r in register["404"]["content"][media]["schema"]["anyOf"]} == {
        "RunnerNotFoundProblem",
        "TenantNotFoundProblem",
    }
    claim = paths["/api/fleet/routes"]["post"]["responses"]
    assert "RunnerRetiredProblem" not in {
        r["$ref"].split("/")[-1] for r in claim["403"]["content"][media]["schema"]["anyOf"]
    }


def test_problem_components_use_serialization_schemas() -> None:
    spec = create_app_for_export(packaged_flag_registry()).openapi()
    _, expected = models_json_schema(
        [(RouteClaimDependencyProblem, "serialization"), (RouteClaimIncompatibleProblem, "serialization")],
        ref_template="#/components/schemas/{model}",
    )
    for name, schema in expected["$defs"].items():
        assert spec["components"]["schemas"][name] == schema


def test_authorize_runtime_variants_and_planted_drift(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    user = seed_user(hub, username="alice", role=Role.CONTRIBUTOR, email="alice@example.com")
    hub.client.cookies.set("bz_session", seed_session(hub, user))
    path = "/api/auth/authorize"
    media = "application/problem+json"
    base = {"client": "runner-missing", "redirect_uri": "https://example.com/callback", "state": "state"}
    observations: list[RefusalObservation] = []
    for params, model in (
        ({**base, "response_mode": "query"}, FederationResponseModeProblem),
        (base, FederationClientRefusedProblem),
        ({**base, "client": "cli"}, CliRedirectRefusedProblem),
        ({**base, "client": "cli", "redirect_uri": "http://127.0.0.1:54321/callback"}, CliPkceRequiredProblem),
    ):
        response = hub.client.get(path, params=params, follow_redirects=False)
        assert response.status_code == 400, response.text
        observations.append(
            RefusalObservation(
                "get", path, response.status_code, response.headers["content-type"], model, response.json()
            )
        )

    assert hub.app is not None
    spec = hub.app.openapi()
    assert all(not refusal_mismatches(spec, observation) for observation in observations)
    models = {observation.model for observation in observations}
    assert unobserved_variants(spec, "get", path, 400, media, models) == set()
    assert unobserved_variants(spec, "get", path, 400, media, models - {CliPkceRequiredProblem}) == {
        "CliPkceRequiredProblem"
    }

    planted = deepcopy(spec)
    content = planted["paths"][path]["get"]["responses"]["400"]["content"]
    content[media]["schema"] = {"$ref": "#/components/schemas/CliRedirectRefusedProblem"}
    assert refusal_mismatches(planted, observations[0]) == ["missing model: FederationResponseModeProblem"]
    content["application/json"] = content.pop(media)
    assert refusal_mismatches(planted, observations[0]) == [f"missing media type: {media}"]


def test_claim_runtime_variants_match_registered_responses(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    seed_runner(hub, "r3", projects=())
    path = "/api/fleet/routes"
    media = "application/problem+json"
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    assert hub.app is not None
    spec = hub.app.openapi()
    observations: list[RefusalObservation] = []

    def claim(runner: str, chunk_id: str) -> Response:
        return hub.client.post(
            path, json={"chunk_id": chunk_id, "runner_id": runner, "workspace_id": "w1", "environment_ids": []}
        )

    # Each request reaches a distinct refusal branch, not merely a compatible body shape.
    def observe(response: Response, status: int, model: type[BaseModel]) -> None:
        observation = observe_refusal(observations, response, "post", path, status, model)
        assert refusal_mismatches(spec, observation) == []

    observe(claim("r1", "ch_missing"), 404, ChunkNotFoundProblem)
    observe(claim("r1", chunk), 409, RouteClaimNotReadyProblem)
    assert hub.client.post(f"/api/chunks/{chunk}/promote").status_code == 202
    assert claim("r1", chunk).status_code == 201
    observe(claim("r2", chunk), 409, RouteClaimConflictProblem)
    incompatible = ingest(hub, [{"source": "default", "ref": "incompatible"}])
    observe(claim("r3", incompatible), 409, RouteClaimIncompatibleProblem)
    terminal = ingest(hub, [{"source": "default", "ref": "terminal"}])
    assert hub.client.post(f"/api/chunks/{terminal}/stop", json={"by": "operator"}).status_code == 202
    observe(claim("r1", terminal), 409, RouteClaimTerminalProblem)
    dependent = ingest(hub, [{"source": "default", "ref": "dependent"}])
    prerequisite = ingest(hub, [{"source": "default", "ref": "prerequisite"}])
    record = hub.services.chunks.record.get(dependent)
    assert record is not None
    hub.services.dependencies.declare(record, PrerequisiteScope(prerequisite), by="user:alice")
    observe(claim("r1", dependent), 409, RouteClaimDependencyProblem)

    models = {observation.model for observation in observations if observation.status == 409}
    assert unobserved_variants(spec, "post", path, 409, media, models) == set()
    assert unobserved_variants(spec, "post", path, 404, media, {ChunkNotFoundProblem}) == {"TenantNotFoundProblem"}


def test_resource_and_host_runtime_refusals_match_registered_responses(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    spec = hub.app.openapi()

    def observe(response: Response, method: str, path: str, status: int, model: type[BaseModel]) -> None:
        assert response.status_code == status, response.text
        observation = RefusalObservation(method, path, status, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(spec, observation) == []

    host_path = "/api/hosts"
    observe(
        hub.client.post(host_path, json={"name": "box"}, headers={"X-Blizzard-Tenant": "ten_missing"}),
        "post",
        host_path,
        404,
        TenantNotFoundProblem,
    )
    host = hub.client.post(host_path, json={"name": "box"})
    assert host.status_code == 201, host.text
    observe(hub.client.post(host_path, json={"name": "box"}), "post", host_path, 409, HostNameTakenProblem)
    runners = "/api/fleet/host/runners"
    observe(
        hub.client.post(
            runners, json={"name": "runner", "projects": ["missing"]}, headers=bearer(host.json()["token"])
        ),
        "post",
        runners,
        422,
        UnknownServedProjectProblem,
    )
    observe(hub.client.get("/api/auth/jwks.json"), "get", "/api/auth/jwks.json", 404, IdpDisabledProblem)
    observe(
        hub.client.get("/api/fleet/system-artifacts/missing/ghost"),
        "get",
        "/api/fleet/system-artifacts/{name}",
        404,
        SystemArtifactNotFoundProblem,
    )
    chunk = hub.client.post("/api/chunks", json={"tokens": ["default:1"]}).json()["chunk_id"]
    observe(
        hub.client.get(f"/api/fleet/chunks/{chunk}/garden/proposals/page"),
        "get",
        "/api/fleet/chunks/{chunk_id}/garden/proposals/page",
        404,
        NoRunContextProblem,
    )
    observe(
        hub.client.post(f"/api/fleet/chunks/{chunk}/route-token", json={"runner_id": "r1"}),
        "post",
        "/api/fleet/chunks/{chunk_id}/route-token",
        404,
        RouteNotFoundProblem,
    )


def test_host_shared_auth_refusals_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    assert hub.app is not None
    path = "/api/hosts"
    spec = hub.app.openapi()
    host_user = seed_user(hub, username="admin", role=Role.ADMIN, email="admin@example.com")
    hub.client.cookies.set("bz_session", seed_session(hub, host_user))
    host = hub.client.post(path, json={"name": "box"})
    assert host.status_code == 201, host.text
    runner_token = seed_runner(hub, "r1")
    reader = seed_user(hub, username="reader", role=Role.GUEST, email="reader@example.com")

    client = TestClient(hub.app)
    cases: list[tuple[Response, int, type[BaseModel]]] = [
        (client.post(path, json={"name": "missing"}), 401, AuthenticationRequiredProblem),
        (
            client.post(path, json={"name": "machine"}, headers=bearer(host.json()["token"])),
            403,
            MachineCredentialForbiddenProblem,
        ),
        (
            client.post(path, json={"name": "runner"}, headers=bearer(runner_token)),
            403,
            RunnerCredentialForbiddenProblem,
        ),
    ]
    client.cookies.set("bz_session", seed_session(hub, reader))
    cases.append((client.post(path, json={"name": "reader"}), 403, PermissionRequiredProblem))
    observed: set[type[BaseModel]] = set()
    for response, status, model in cases:
        assert response.status_code == status, response.text
        observation = RefusalObservation("post", path, status, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(spec, observation) == []
        if status == 403:
            observed.add(model)
    assert unobserved_variants(spec, "post", path, 403, "application/problem+json", observed) == set()


def test_fleet_queue_and_release_runtime_refusals(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    spec = hub.app.openapi()
    queue = "/api/fleet/queue/peek"
    response = hub.client.get(queue, params={"project": "missing"})
    assert response.status_code == 422, response.text
    observation = RefusalObservation(
        "get", queue, 422, response.headers["content-type"], UnknownProjectProblem, response.json()
    )
    assert refusal_mismatches(spec, observation) == []
    assert unobserved_variants(spec, "get", queue, 422, "application/problem+json", {UnknownProjectProblem}) == set()

    seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    chunk = ingest(hub, [{"source": "default", "ref": "release"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    path = "/api/fleet/chunks/{chunk_id}/release"
    url = f"/api/fleet/chunks/{chunk}/release"
    for runner, epoch, status, model in (("r2", 1, 403, RouteNotHeldProblem), ("r1", 0, 409, ReleaseProblem)):
        response = hub.client.post(
            url,
            json={
                "runner_id": runner,
                "epoch": epoch,
                "reason": "throttled",
                "route_token": claim.json()["route_token"],
            },
        )
        assert response.status_code == status, response.text
        observation = RefusalObservation("post", path, status, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(spec, observation) == []
        if status == 409:
            assert response.json()["reason"] == "stale"
            assert unobserved_variants(spec, "post", path, status, "application/problem+json", {model}) == set()


def test_fleet_shared_dependencies_observed_on_queue(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    spec = hub.app.openapi()
    path = "/api/fleet/queue/peek"
    token = seed_runner(hub, "runner-a")
    client = TestClient(hub.app)
    assert client.get(path, headers=bearer(token)).status_code == 200
    missing = client.get(path)
    unresolved = client.get(path, headers=bearer("not-a-real-token"))
    assert hub.client.post("/api/runners/runner-a/token-revocations", json={"by": "operator"}).status_code == 201
    revoked = client.get(path, headers=bearer(token))
    observed: set[type[BaseModel]] = set()
    for response, model in (
        (missing, AuthenticationRequiredProblem),
        (unresolved, AuthenticationUnresolvedProblem),
        (revoked, AuthenticationRevokedProblem),
    ):
        assert response.status_code == 401, response.text
        observation = RefusalObservation("get", path, 401, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(spec, observation) == []
        observed.add(model)
    assert unobserved_variants(spec, "get", path, 401, "application/problem+json", observed) == set()

    live_token = seed_runner(hub, "runner-b")
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    registry.record_lifecycle("runner-b", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0)
    retired = client.get(path, headers=bearer(live_token))
    assert retired.status_code == 403, retired.text
    observation = RefusalObservation(
        "get", path, 403, retired.headers["content-type"], RunnerRetiredProblem, retired.json()
    )
    assert refusal_mismatches(spec, observation) == []
    assert unobserved_variants(spec, "get", path, 403, "application/problem+json", {RunnerRetiredProblem}) == set()


def test_retired_host_and_unwired_store_dependencies_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    path = "/api/fleet/host"
    host = hub.client.post("/api/hosts", json={"name": "box"})
    assert host.status_code == 201, host.text
    host_id = host.json()["host_id"]
    assert hub.client.post(f"/api/hosts/{host_id}/retire", json={}).status_code == 200
    store = HostRegistryStore(hub_store_connections(hub.engine))
    store.rotate_token(HostTokenRotation(host_id, TokenHash("late").hex, hub.clock.now(), "racer"))
    response = hub.client.get(path, headers=bearer("late"))
    assert response.status_code == 403, response.text
    observation = RefusalObservation(
        "get", path, 403, response.headers["content-type"], HostRetiredProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(hub.app.openapi(), "get", path, 403, "application/problem+json", {HostRetiredProblem})
        == set()
    )

    app = create_app_for_export(packaged_flag_registry())
    response = TestClient(app).post("/api/hosts", json={"name": "box"})
    assert response.status_code == 503, response.text
    observation = RefusalObservation(
        "post", "/api/hosts", 503, response.headers["content-type"], HubStoreUnavailableProblem, response.json()
    )
    assert refusal_mismatches(app.openapi(), observation) == []
    assert (
        unobserved_variants(
            app.openapi(), "post", "/api/hosts", 503, "application/problem+json", {HubStoreUnavailableProblem}
        )
        == set()
    )


def test_rekey_not_holder_matches_declared_variant(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    chunk = ingest(hub, [{"source": "default", "ref": "rekey"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    path = "/api/fleet/chunks/{chunk_id}/route-token"
    response = hub.client.post(f"/api/fleet/chunks/{chunk}/route-token", json={"runner_id": "r2"})
    assert response.status_code == 403, response.text
    assert hub.app is not None
    observation = RefusalObservation(
        "post", path, 403, response.headers["content-type"], RouteNotHeldProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert unobserved_variants(
        hub.app.openapi(), "post", path, 403, "application/problem+json", {RouteNotHeldProblem}
    ) == {"RunnerRetiredProblem"}
    release_path = "/api/fleet/chunks/{chunk_id}/release"
    release = hub.client.post(
        f"/api/fleet/chunks/{chunk}/release", json={"runner_id": "r2", "epoch": 1, "reason": "throttled"}
    )
    assert release.status_code == 403, release.text
    observation = RefusalObservation(
        "post", release_path, 403, release.headers["content-type"], RouteNotHeldProblem, release.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    registry.record_lifecycle("r2", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0)
    for suffix, body in (
        ("route-token", {"runner_id": "r2"}),
        ("release", {"runner_id": "r2", "epoch": 1, "reason": "throttled"}),
    ):
        template = f"/api/fleet/chunks/{{chunk_id}}/{suffix}"
        response = hub.client.post(f"/api/fleet/chunks/{chunk}/{suffix}", json=body)
        assert response.status_code == 403, response.text
        observation = RefusalObservation(
            "post", template, 403, response.headers["content-type"], RunnerRetiredProblem, response.json()
        )
        assert refusal_mismatches(hub.app.openapi(), observation) == []
        assert (
            unobserved_variants(
                hub.app.openapi(),
                "post",
                template,
                403,
                "application/problem+json",
                {RouteNotHeldProblem, RunnerRetiredProblem},
            )
            == set()
        )


def test_claim_runner_gate_refusals_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(
        tmp_path, auto_register_claimants=False, hub_version="0.3", version_window=VersionWindow(floor="0.2")
    )
    assert hub.app is not None
    path = "/api/fleet/routes"
    chunk = ingest(hub, [{"source": "default", "ref": "runner-gates"}])
    seed_runner(hub, "paused")
    seed_runner(hub, "retired")
    seed_runner(hub, "old")
    registration = hub.client.post(
        "/api/fleet/runners", json={"runner_id": "old", "workspace_id": "w1", "version": "0.1.4"}
    )
    assert registration.status_code == 409, registration.text
    observation = RefusalObservation(
        "post",
        "/api/fleet/runners",
        409,
        registration.headers["content-type"],
        RunnerVersionProblem,
        registration.json(),
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(
            hub.app.openapi(), "post", "/api/fleet/runners", 409, "application/problem+json", {RunnerVersionProblem}
        )
        == set()
    )
    assert hub.client.post("/api/runners/paused/pause", json={"by": "operator"}).status_code == 200
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    registry.record_lifecycle("retired", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0)
    observed: set[type[BaseModel]] = set()
    for runner, model in (
        ("paused", RouteClaimPausedProblem),
        ("retired", ClaimRunnerRetiredProblem),
        ("unregistered", ClaimRunnerUnregisteredProblem),
        ("old", ClaimRunnerVersionRefusedProblem),
    ):
        response = hub.client.post(
            path, json={"chunk_id": chunk, "runner_id": runner, "workspace_id": "w1", "environment_ids": []}
        )
        assert response.status_code == 403, response.text
        assert response.json()["runner_id"] == runner
        observation = RefusalObservation("post", path, 403, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []
        observed.add(model)
    assert unobserved_variants(hub.app.openapi(), "post", path, 403, "application/problem+json", observed) == set()


def test_tenant_dependency_refusals_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    host_path = "/api/hosts"
    host = hub.client.post(host_path, json={"name": "box"})
    assert host.status_code == 201, host.text
    other = hub.add_tenant("other")
    fleet_path = "/api/fleet/host"
    mismatch = hub.client.get(fleet_path, headers={**bearer(host.json()["token"]), "X-Blizzard-Tenant": other.tenant})
    required = hub.client.post(host_path, json={"name": "unnamed"})
    for response, method, path, status, model in (
        (mismatch, "get", fleet_path, 400, TenantCredentialMismatchProblem),
        (required, "post", host_path, 409, TenantRequiredProblem),
    ):
        assert response.status_code == status, response.text
        observation = RefusalObservation(method, path, status, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(
            hub.app.openapi(), "get", fleet_path, 400, "application/problem+json", {TenantCredentialMismatchProblem}
        )
        == set()
    )


def test_claim_tenant_credential_mismatch_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    seed_runner(hub, "r1")
    path = "/api/fleet/routes"
    response = hub.client.post(
        path,
        json={"chunk_id": "ch_missing", "runner_id": "r1", "workspace_id": "w1", "environment_ids": []},
        headers={"X-Blizzard-Tenant": "ten_missing"},
    )
    assert response.status_code == 400, response.text
    observation = RefusalObservation(
        "post", path, 400, response.headers["content-type"], TenantCredentialMismatchProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(
            hub.app.openapi(), "post", path, 400, "application/problem+json", {TenantCredentialMismatchProblem}
        )
        == set()
    )


def test_host_runner_retirement_holding_route_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    host = hub.client.post("/api/hosts", json={"name": "box"})
    assert host.status_code == 201, host.text
    seed_runner(hub, "r1")
    adoption = hub.client.post(f"/api/hosts/{host.json()['host_id']}/adoptions", json={"runner_ids": ["r1"]})
    assert adoption.status_code == 200, adoption.text
    chunk = ingest(hub, [{"source": "default", "ref": "held"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    path = "/api/fleet/host/runners/{runner_id}/retire"
    response = hub.client.post("/api/fleet/host/runners/r1/retire", json={}, headers=bearer(host.json()["token"]))
    assert response.status_code == 409, response.text
    assert chunk in response.json()["detail"]
    observation = RefusalObservation(
        "post", path, 409, response.headers["content-type"], RunnerHoldsRoutesProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert unobserved_variants(
        hub.app.openapi(), "post", path, 409, "application/problem+json", {RunnerHoldsRoutesProblem}
    ) == {"RunnerLifecycleConflictProblem", "RunnerRetireInterruptedProblem"}


def test_missing_pinned_graph_on_claim_and_envelope_matches_openapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    seed_runner(hub, "r1")
    chunk = ingest(hub, [{"source": "default", "ref": "missing-graph"}])
    record = hub.services.chunks.record.get(chunk)
    assert record is not None
    graphs = hub.services.graphs
    get_graph = graphs.get
    assert get_graph(record.graph_id) is not None

    def missing_graph(graph_id: str) -> Graph | None:
        return None if graph_id == record.graph_id else get_graph(graph_id)

    monkeypatch.setattr(graphs, "get", missing_graph)
    cases = (
        (
            "post",
            "/api/fleet/routes",
            hub.client.post(
                "/api/fleet/routes",
                json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []},
            ),
        ),
        ("get", "/api/fleet/chunks/{chunk_id}/envelope", hub.client.get(f"/api/fleet/chunks/{chunk}/envelope")),
    )
    for method, path, response in cases:
        assert response.status_code == 500, response.text
        assert response.json()["detail"] == "chunk's pinned graph is missing"
        observation = RefusalObservation(
            method, path, 500, response.headers["content-type"], PinnedGraphMissingProblem, response.json()
        )
        assert refusal_mismatches(hub.app.openapi(), observation) == []
        assert (
            unobserved_variants(
                hub.app.openapi(), method, path, 500, "application/problem+json", {PinnedGraphMissingProblem}
            )
            == set()
        )


@pytest.mark.parametrize("interrupted", [False, True])
def test_host_runner_retirement_lifecycle_races_match_openapi(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, interrupted: bool
) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    host = hub.client.post("/api/hosts", json={"name": "box"})
    assert host.status_code == 201, host.text
    seed_runner(hub, "r1")
    adoption = hub.client.post(f"/api/hosts/{host.json()['host_id']}/adoptions", json={"runner_ids": ["r1"]})
    assert adoption.status_code == 200, adoption.text
    chunk = ingest(hub, [{"source": "default", "ref": "race"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    fleet = hub.services.fleet
    if interrupted:
        release = hub.services.detach.release_held

        def reinstate_before_release(*args: Any, **kwargs: Any) -> Any:
            registration = registry.get_runner("r1")
            assert registration is not None and registration.retired
            registry.record_lifecycle(
                "r1", retired=False, at=hub.clock.now(), by="racer", expected_lifecycle=registration.lifecycle_version
            )
            return release(*args, **kwargs)

        monkeypatch.setattr(hub.services.detach, "release_held", reinstate_before_release)
        model = RunnerRetireInterruptedProblem
    else:
        holdings = fleet.holdings

        def retire_after_read(runner_id: str) -> Any:
            routes = holdings(runner_id)
            registry.record_lifecycle(runner_id, retired=True, at=hub.clock.now(), by="racer", expected_lifecycle=0)
            return routes

        monkeypatch.setattr(fleet, "holdings", retire_after_read)
        model = RunnerLifecycleConflictProblem
    path = "/api/fleet/host/runners/{runner_id}/retire"
    response = hub.client.post(
        "/api/fleet/host/runners/r1/retire", json={"force": True}, headers=bearer(host.json()["token"])
    )
    assert response.status_code == 409, response.text
    observation = RefusalObservation("post", path, 409, response.headers["content-type"], model, response.json())
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert hub.services.chunks.route.route_of(chunk) is not None
    assert (
        unobserved_variants(
            hub.app.openapi(),
            "post",
            path,
            409,
            "application/problem+json",
            {RunnerHoldsRoutesProblem, RunnerLifecycleConflictProblem, RunnerRetireInterruptedProblem},
        )
        == set()
    )


def test_fleet_missing_resource_reads_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    seed_runner(hub, "r1")
    for url, path, model in (
        ("/api/fleet/questions/q_missing", "/api/fleet/questions/{question_id}", QuestionNotFoundProblem),
        ("/api/fleet/chunks/ch_missing/envelope", "/api/fleet/chunks/{chunk_id}/envelope", ChunkNotFoundProblem),
    ):
        response = hub.client.get(url)
        assert response.status_code == 404, response.text
        observation = RefusalObservation("get", path, 404, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []
        assert unobserved_variants(hub.app.openapi(), "get", path, 404, "application/problem+json", {model}) == {
            "TenantNotFoundProblem"
        }


def test_rekey_terminal_route_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    seed_runner(hub, "r1")
    graph = """name: default-delivery
entry: build
nodes:
  build:
    executor: runner
    prompt: Build.
    judgement:
      prompt: Assess.
      choices:
        pass:
          description: Complete.
          to: done
"""
    created_graph = hub.client.post("/api/graphs", json={"definition_yaml": graph})
    assert created_graph.status_code == 201, created_graph.text
    chunk = ingest(hub, [{"source": "default", "ref": "terminal-rekey"}])
    claim = hub.client.post(
        "/api/fleet/routes", json={"chunk_id": chunk, "runner_id": "r1", "workspace_id": "w1", "environment_ids": []}
    )
    assert claim.status_code == 201, claim.text
    node = hub.client.get(f"/api/chunks/{chunk}").json()["current_node_id"]
    report_lease(hub, chunk, epoch=1, seq=1, runner_id="r1")
    done = hub.client.post(
        f"/api/fleet/chunks/{chunk}/completions",
        json={"choice": "pass", "epoch": 1, "runner_id": "r1", "from_node_id": node, "artifacts": []},
    )
    assert done.status_code == 200 and done.json()["outcome"] == "done", done.text
    assert hub.services.chunks.route.route_of(chunk) is not None
    path = "/api/fleet/chunks/{chunk_id}/route-token"
    response = hub.client.post(f"/api/fleet/chunks/{chunk}/route-token", json={"runner_id": "r1"})
    assert response.status_code == 409, response.text
    assert response.json()["detail"] == f"chunk {chunk} is done, its route confers no tenure"
    observation = RefusalObservation(
        "post", path, 409, response.headers["content-type"], RouteTerminalProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(hub.app.openapi(), "post", path, 409, "application/problem+json", {RouteTerminalProblem})
        == set()
    )


def test_fleet_reads_and_mutation_missing_resources_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    seed_runner(hub, "r1")
    chunk = ingest(hub, [{"source": "default", "ref": "no-proposal"}])
    cases = (
        ("get", "/api/fleet/scopes?chunk_id=ch_missing", "/api/fleet/scopes", ChunkNotFoundProblem),
        (
            "get",
            "/api/fleet/chunks/ch_missing/garden/findings",
            "/api/fleet/chunks/{chunk_id}/garden/findings",
            ChunkNotFoundProblem,
        ),
        (
            "get",
            f"/api/fleet/chunks/{chunk}/garden/findings",
            "/api/fleet/chunks/{chunk_id}/garden/findings",
            NoRunContextProblem,
        ),
        ("get", "/api/fleet/chunks/ch_missing/findings", "/api/fleet/chunks/{chunk_id}/findings", ChunkNotFoundProblem),
        (
            "get",
            f"/api/fleet/chunks/{chunk}/findings",
            "/api/fleet/chunks/{chunk_id}/findings",
            NoAnsweredProposalProblem,
        ),
        (
            "get",
            f"/api/fleet/chunks/{chunk}/findings/f_missing",
            "/api/fleet/chunks/{chunk_id}/findings/{finding_id}",
            NoAnsweredProposalProblem,
        ),
        (
            "post",
            "/api/fleet/chunks/ch_missing/hub-advance",
            "/api/fleet/chunks/{chunk_id}/hub-advance",
            ChunkNotFoundProblem,
        ),
        ("post", "/api/fleet/chunks/ch_missing/release", "/api/fleet/chunks/{chunk_id}/release", ChunkNotFoundProblem),
    )
    for method, url, path, model in cases:
        if url.endswith("/release"):
            response = hub.client.request(method, url, json={"runner_id": "r1", "epoch": 1, "reason": "throttled"})
        else:
            response = hub.client.request(method, url)
        assert response.status_code == 404, response.text
        observation = RefusalObservation(method, path, 404, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []


def test_fleet_runner_decisions_ownership_and_retirement_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    token = seed_runner(hub, "r1")
    seed_runner(hub, "r2")
    client = TestClient(hub.app)
    path = "/api/fleet/runners/{runner_id}/decisions"
    assert client.get("/api/fleet/runners/r1/decisions", headers=bearer(token)).status_code == 200
    foreign = client.get("/api/fleet/runners/r2/decisions", headers=bearer(token))
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    registry.record_lifecycle("r1", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0)
    retired = client.get("/api/fleet/runners/r1/decisions", headers=bearer(token))
    for response, model in ((foreign, RunnerOwnershipProblem), (retired, RunnerRetiredProblem)):
        assert response.status_code == 403, response.text
        observation = RefusalObservation("get", path, 403, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert (
        unobserved_variants(
            hub.app.openapi(),
            "get",
            path,
            403,
            "application/problem+json",
            {RunnerOwnershipProblem, RunnerRetiredProblem},
        )
        == set()
    )


def test_fleet_mutation_shared_auth_refusals_match_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    token = seed_runner(hub, "r1")
    client = TestClient(hub.app)
    path = "/api/fleet/chunks/{chunk_id}/hub-advance"
    url = "/api/fleet/chunks/ch_missing/hub-advance"
    missing = client.post(url)
    unresolved = client.post(url, headers=bearer("not-a-real-token"))
    assert hub.client.post("/api/runners/r1/token-revocations", json={"by": "operator"}).status_code == 201
    revoked = client.post(url, headers=bearer(token))
    observed: set[type[BaseModel]] = set()
    for response, model in (
        (missing, AuthenticationRequiredProblem),
        (unresolved, AuthenticationUnresolvedProblem),
        (revoked, AuthenticationRevokedProblem),
    ):
        assert response.status_code == 401, response.text
        observation = RefusalObservation("post", path, 401, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []
        observed.add(model)
    assert unobserved_variants(hub.app.openapi(), "post", path, 401, "application/problem+json", observed) == set()


def test_finding_outside_answered_proposal_matches_openapi(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    connections = hub_store_connections(hub.engine)
    now = datetime(2026, 9, 4, 12, tzinfo=UTC)
    seed_scope(connections, "blizzard", at=now)
    findings = FindingStore(connections)
    for finding_id in ("fin_inside", "fin_outside"):
        findings.add(
            finding_id,
            project_id=default_project(hub.engine),
            routine_name="nightly",
            scope_slug="blizzard",
            class_="stale-docstring",
            locus="a.py:1",
            summary="s",
            introduced=None,
            at=now,
        )
    GardenProposalStore(connections).create(
        "gprop_1",
        project_id=default_project(hub.engine),
        origin=GardenProposalOrigin.ROUTINE_RUN,
        routine_name="nightly",
        class_="fix-the-source",
        title="t",
        body="b",
        findings=["fin_inside"],
        at=now,
    )
    accepted = hub.client.post("/api/garden-proposals/gprop_1/accept", json={})
    assert accepted.status_code == 200, accepted.text
    chunk = accepted.json()["chunk_id"]
    inside = hub.client.get(f"/api/fleet/chunks/{chunk}/findings/fin_inside")
    assert inside.status_code == 200, inside.text
    response = hub.client.get(f"/api/fleet/chunks/{chunk}/findings/fin_outside")
    assert response.status_code == 404, response.text
    assert "not among the findings" in response.json()["detail"]
    path = "/api/fleet/chunks/{chunk_id}/findings/{finding_id}"
    observation = RefusalObservation(
        "get", path, 404, response.headers["content-type"], FindingNotInChunkProblem, response.json()
    )
    assert refusal_mismatches(hub.app.openapi(), observation) == []
    assert unobserved_variants(
        hub.app.openapi(), "get", path, 404, "application/problem+json", {FindingNotInChunkProblem}
    ) == {"ChunkNotFoundProblem", "NoAnsweredProposalProblem", "TenantNotFoundProblem"}
    plain = ingest(hub, [{"source": "default", "ref": "plain"}])
    observed: set[type[BaseModel]] = {FindingNotInChunkProblem}
    for chunk_id, model in (("ch_missing", ChunkNotFoundProblem), (plain, NoAnsweredProposalProblem)):
        response = hub.client.get(f"/api/fleet/chunks/{chunk_id}/findings/fin_inside")
        assert response.status_code == 404, response.text
        observation = RefusalObservation("get", path, 404, response.headers["content-type"], model, response.json())
        assert refusal_mismatches(hub.app.openapi(), observation) == []
        observed.add(model)
    assert unobserved_variants(hub.app.openapi(), "get", path, 404, "application/problem+json", observed) == {
        "TenantNotFoundProblem"
    }
