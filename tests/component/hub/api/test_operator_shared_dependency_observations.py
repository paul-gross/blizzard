"""Observe the shared dependency refusals of every operation the operator CLI and the board call."""

import re
from collections.abc import Callable
from pathlib import Path
from typing import Any

from fastapi.testclient import TestClient
from httpx2 import Response
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.cli.config_inputs import packaged_flag_registry
from blizzard.hub.api.problem_declarations import OPERATOR_DEPENDENCY_DECLARATIONS
from blizzard.hub.app import create_app_for_export
from blizzard.wire.auth import AuthenticationRequiredProblem, PermissionRequiredProblem
from blizzard.wire.flags import FlagOffProblem
from blizzard.wire.problem import HubStoreUnavailableProblem
from blizzard.wire.project import ProjectRequiredProblem
from blizzard.wire.tenant import TenantCredentialMismatchProblem, TenantNotFoundProblem, TenantRequiredProblem
from tests.support.hub_request_auth import bearer, cookie
from tests.support.operator_dependency_discovery import operator_dependencies
from tests.support.refusal_observation import RefusalObservation, observe_refusal, refusal_mismatches
from tests.support.scaffolding import HubHarness, build_hub, link_project, seed_project, seed_session, seed_user

Operation = tuple[str, str]
_PARAMETER = re.compile(r"\{([^}:]+)(?::[^}]+)?\}")


def _declaring(status: int, model: type[BaseModel]) -> set[Operation]:
    return {
        (declaration.method, declaration.path)
        for declaration in OPERATOR_DEPENDENCY_DECLARATIONS
        if (declaration.status_code, declaration.model) == (status, model)
    }


def _url(path: str, **values: str) -> str:
    return _PARAMETER.sub(lambda match: values.get(match.group(1), "missing"), path)


def _schema_path(path: str) -> str:
    return path.replace("{name:path}", "{name}")


def _observe_each(
    hub: HubHarness,
    operations: set[Operation],
    status: int,
    model: type[BaseModel],
    send: Callable[[TestClient, str, str], Response],
    detail: Any = None,
) -> list[str]:
    """Send each operation's refused request and return every mismatch with its declaration."""
    assert hub.app is not None
    client = TestClient(hub.app)
    spec = hub.app.openapi()
    mismatches: list[str] = []
    for method, path in sorted(operations):
        response = send(client, method, path)
        if (response.status_code, response.headers["content-type"]) != (status, "application/problem+json"):
            mismatches.append(f"{method} {path}: answered {response.status_code} {response.text}")
            continue
        observations: list[RefusalObservation] = []
        observe_refusal(observations, response, method, _schema_path(path), status, model)
        if detail is not None:
            assert response.json()["detail"] == detail, (method, path, response.text)
        mismatches.extend(f"{method} {path}: {failure}" for failure in refusal_mismatches(spec, observations[0]))
    return mismatches


def test_each_dependency_declares_exactly_the_operations_reaching_it() -> None:
    reached = operator_dependencies(create_app_for_export(packaged_flag_registry()))
    assert _declaring(401, AuthenticationRequiredProblem) == reached.authentication
    assert _declaring(403, PermissionRequiredProblem) == reached.permission
    assert _declaring(400, TenantCredentialMismatchProblem) == reached.tenant
    assert _declaring(404, TenantNotFoundProblem) == reached.tenant
    assert _declaring(409, TenantRequiredProblem) == reached.tenant
    assert _declaring(422, ProjectRequiredProblem) == reached.project
    assert _declaring(404, FlagOffProblem) == reached.flag
    assert _declaring(503, HubStoreUnavailableProblem) == reached.store


def test_authentication_refusals_match_each_operation(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    mismatches = _observe_each(
        hub,
        _declaring(401, AuthenticationRequiredProblem),
        401,
        AuthenticationRequiredProblem,
        lambda client, method, path: client.request(method, _url(path), json={}),
        "authentication required",
    )
    assert mismatches == []


def test_permission_refusals_match_each_operation(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode="oauth")
    guest = cookie(seed_session(hub, seed_user(hub, username="guest", role=Role.GUEST)))
    mismatches = _observe_each(
        hub,
        _declaring(403, PermissionRequiredProblem),
        403,
        PermissionRequiredProblem,
        lambda client, method, path: client.request(method, _url(path, project="default"), headers=guest, json={}),
    )
    assert mismatches == []


def test_tenant_refusals_match_each_operation(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    created = hub.client.post("/api/hosts", json={"name": "box"})
    assert created.status_code == 201, created.text
    host = bearer(created.json()["token"])
    operations = _declaring(404, TenantNotFoundProblem)
    unknown = _observe_each(
        hub,
        operations,
        404,
        TenantNotFoundProblem,
        lambda client, method, path: client.request(
            method, _url(path), headers={"X-Blizzard-Tenant": "tn_missing"}, json={}
        ),
        "no such tenant",
    )
    other = hub.add_tenant("other")
    mismatched = _observe_each(
        hub,
        operations,
        400,
        TenantCredentialMismatchProblem,
        lambda client, method, path: client.request(
            method, _url(path), headers={**host, "X-Blizzard-Tenant": other.tenant}, json={}
        ),
    )
    required = _observe_each(
        hub,
        operations,
        409,
        TenantRequiredProblem,
        lambda client, method, path: client.request(method, _url(path), json={}),
        {"code": "tenant_required", "message": "name the tenant to act in with X-Blizzard-Tenant"},
    )
    assert unknown + mismatched + required == []


def test_store_unavailable_refusals_match_each_operation(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    assert hub.app is not None
    hub.app.state.services = None
    mismatches = _observe_each(
        hub,
        _declaring(503, HubStoreUnavailableProblem),
        503,
        HubStoreUnavailableProblem,
        lambda client, method, path: client.request(method, _url(path), json={}),
    )
    assert mismatches == []


_SPAN = {"since": "2026-01-01T00:00:00Z", "until": "2026-02-01T00:00:00Z"}
#: A request reaching the project requirement, for each operation that needs more than its path to reach it.
_PROJECT_REQUESTS: dict[Operation, dict[str, Any]] = {
    ("GET", "/api/findings"): {"params": {"routine": "r"}},
    ("GET", "/api/findings/classes"): {"params": {"routine": "r"}},
    ("GET", "/api/routines/proposal-counts"): {"params": {**_SPAN, "routine": "r"}},
    ("GET", "/api/routines/trend"): {"params": {**_SPAN, "routine": "r", "introduced_boundary": _SPAN["since"]}},
    ("POST", "/api/chunks"): {"json": {"tokens": ["default:1"]}},
    ("POST", "/api/garden-proposals"): {"json": {"class": "c", "title": "t", "body": "b"}},
    ("POST", "/api/work-sources/{source}/items"): {"json": {"title": "t", "body": "b"}},
}


def test_project_required_refusals_match_each_operation(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    link_project(hub.engine, project=seed_project(hub.engine, "second"), source="default")
    mismatches = _observe_each(
        hub,
        _declaring(422, ProjectRequiredProblem),
        422,
        ProjectRequiredProblem,
        lambda client, method, path: client.request(
            method, _url(path, source="hub"), **_PROJECT_REQUESTS.get((method, path), {"json": {}})
        ),
    )
    assert mismatches == []
