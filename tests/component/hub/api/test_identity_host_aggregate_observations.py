"""Aggregate the declared problem variants against real route responses."""

from collections.abc import Sequence
from pathlib import Path
from typing import cast

import pytest
from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.hub.domain.runners.registration import IWriteRunnerRegistry, RunnerRegistration
from blizzard.wire.auth import (
    AuthenticationRequiredProblem,
    IdpDisabledProblem,
    MachineCredentialForbiddenProblem,
    PermissionRequiredProblem,
    RunnerCredentialForbiddenProblem,
)
from blizzard.wire.host import HostNameTakenProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.runner import (
    RunnerAlreadyHostedProblem,
    RunnerLifecycleConflictProblem,
    RunnerNotFoundProblem,
    RunnerRetiredProblem,
)
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem, TenantRequiredProblem
from tests.support.hub_request_auth import bearer, cookie
from tests.support.refusal_observation import RefusalObservation, coverage_mismatches, observe_refusal
from tests.support.scaffolding import build_hub, seed_runner, seed_session, seed_user


def test_identity_and_host_problem_variants_are_aggregated(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    (tmp_path / "none").mkdir()
    (tmp_path / "oauth").mkdir()
    hub = build_hub(tmp_path / "none")
    oauth = build_hub(tmp_path / "oauth", auth_mode="oauth")
    assert hub.app is not None and oauth.app is not None
    observations: list[RefusalObservation] = []

    def observe(response: Response, method: str, path: str, status: int, model: type[BaseModel]) -> None:
        observe_refusal(observations, response, method, path, status, model)

    jwks = "/api/auth/jwks.json"
    me = "/api/me"
    hosts = "/api/hosts"
    observe(hub.client.get(jwks), "get", jwks, 404, IdpDisabledProblem)
    observe(oauth.client.get(me), "get", me, 401, AuthenticationRequiredProblem)
    observe(oauth.client.post(hosts, json={"name": "x"}), "post", hosts, 401, AuthenticationRequiredProblem)
    guest = seed_user(oauth, username="guest", role=Role.GUEST)
    guest_cookie = cookie(seed_session(oauth, guest))
    observe(
        oauth.client.post(hosts, json={"name": "x"}, headers=guest_cookie),
        "post",
        hosts,
        403,
        PermissionRequiredProblem,
    )
    observe(
        oauth.client.post(hosts, json={"name": "x"}, headers={**guest_cookie, "X-Blizzard-Tenant": "missing"}),
        "post",
        hosts,
        404,
        TenantNotFoundProblem,
    )

    runner = seed_runner(hub, "runner")
    created = hub.client.post(hosts, json={"name": "box", "adopt": ["runner"]})
    assert created.status_code == 201, created.text
    machine = created.json()["token"]
    observe(hub.client.post(hosts, json={"name": "box"}), "post", hosts, 409, HostNameTakenProblem)
    observe(
        hub.client.post(hosts, json={"name": "other", "adopt": ["missing"]}), "post", hosts, 404, RunnerNotFoundProblem
    )
    observe(
        hub.client.post(hosts, json={"name": "other", "adopt": ["runner"]}),
        "post",
        hosts,
        409,
        RunnerAlreadyHostedProblem,
    )
    seed_runner(hub, "retired")
    cast(IWriteRunnerRegistry, hub.services.registry).record_lifecycle(
        "retired", retired=True, at=hub.clock.now(), by="operator", expected_lifecycle=0
    )
    observe(
        hub.client.post(hosts, json={"name": "other", "adopt": ["retired"]}), "post", hosts, 409, RunnerRetiredProblem
    )
    seed_runner(hub, "racing")
    registry = cast(IWriteRunnerRegistry, hub.services.registry)
    runners_named = hub.services.host_enrollment._runners_named  # pyright: ignore[reportPrivateUsage]  # interleave the race

    def retire_after_read(runner_ids: Sequence[str]) -> list[RunnerRegistration]:
        runners: list[RunnerRegistration] = runners_named(runner_ids)
        registry.record_lifecycle("racing", retired=True, at=hub.clock.now(), by="racer", expected_lifecycle=0)
        return runners

    with monkeypatch.context() as patch:
        patch.setattr(hub.services.host_enrollment, "_runners_named", retire_after_read)
        conflict = hub.client.post(hosts, json={"name": "other", "adopt": ["racing"]})
    observe(conflict, "post", hosts, 409, RunnerLifecycleConflictProblem)
    assert "racing" in conflict.json()["detail"]
    created_after_conflict = hub.client.post(hosts, json={"name": "other"})
    assert created_after_conflict.status_code == 201, created_after_conflict.text
    client = TestClient(hub.app)
    observe(
        client.post(hosts, json={"name": "x"}, headers=bearer(runner)),
        "post",
        hosts,
        403,
        RunnerCredentialForbiddenProblem,
    )
    observe(
        client.post(hosts, json={"name": "x"}, headers=bearer(machine)),
        "post",
        hosts,
        403,
        MachineCredentialForbiddenProblem,
    )
    other = hub.add_tenant("other")
    observe(
        client.post(hosts, json={"name": "x"}, headers={**bearer(machine), "X-Blizzard-Tenant": other.tenant}),
        "post",
        hosts,
        400,
        TenantCredentialMismatchProblem,
    )
    observe(hub.client.post(hosts, json={"name": "x"}), "post", hosts, 409, TenantRequiredProblem)
    for app, path, method in ((hub.app, hosts, "post"), (oauth.app, jwks, "get"), (oauth.app, me, "get")):
        services = app.state.services
        app.state.services = None
        response = TestClient(app).request(method, path, json={"name": "x"})
        observe(response, method, path, 503, HubStoreUnavailableProblem)
        app.state.services = services

    assert coverage_mismatches(hub.app.openapi(), {("get", jwks), ("get", me), ("post", hosts)}, observations) == []
