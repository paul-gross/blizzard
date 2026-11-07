"""The configuration-record, secret, member, invitation, tenant, hub-administrator, user and graph refusals answer
typed problems whose ``detail`` is the value the bare 4xx carried (component tier)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import httpx2
from pydantic import BaseModel

from blizzard.auth_core import Role
from blizzard.hub.config import AUTH_MODE_OAUTH
from blizzard.wire import problem_types_admin as t
from blizzard.wire.cadence import RevisionConflictProblem
from blizzard.wire.problem import REVISION_CONFLICT
from tests.support.refusal_observation import declared_refusal_mismatches
from tests.support.scaffolding import (
    HubHarness,
    build_hub,
    default_tenant,
    ensure_fixture_secret,
    seed_hub_admin,
    seed_session,
    seed_user,
)


def _hub_admin_headers(hub: HubHarness) -> dict[str, str]:
    user = seed_user(hub, username="hal", role=Role.PENDING)
    seed_hub_admin(hub.engine, user.user_id)
    return {"Authorization": f"Bearer {seed_session(hub, user)}"}


def _spec(hub: HubHarness) -> dict[str, Any]:
    assert hub.app is not None
    return hub.app.openapi()


def _refused(
    hub: HubHarness,
    response: httpx2.Response,
    method: str,
    path: str,
    status: int,
    model: type[BaseModel],
    type_: str,
    detail: object,
) -> None:
    """``response`` is ``method path``'s declared problem of ``model``, its ``detail`` exactly ``detail``."""
    assert declared_refusal_mismatches(_spec(hub), response, method.lower(), path, status, model) == []
    body: dict[str, Any] = response.json()
    assert (body["type"], body["status"], body["detail"]) == (type_, status, detail)


def test_an_unknown_record_on_a_path_is_a_problem_naming_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    for method, path, template, status, model, type_, detail in (
        (
            "get",
            "/api/projects/nope",
            "/api/projects/{project}",
            404,
            t.ProjectNotFoundProblem,
            t.PROJECT_NOT_FOUND,
            "unknown project 'nope'",
        ),
        (
            "get",
            "/api/forges/nope",
            "/api/forges/{forge}",
            404,
            t.ForgeNotFoundProblem,
            t.FORGE_NOT_FOUND,
            "unknown forge 'nope'",
        ),
        (
            "get",
            "/api/repositories/nope",
            "/api/repositories/{repository}",
            404,
            t.RepositoryNotFoundProblem,
            t.REPOSITORY_NOT_FOUND,
            "unknown repository 'nope'",
        ),
        (
            "get",
            "/api/work-sources/nope",
            "/api/work-sources/{source}",
            404,
            t.WorkSourceNotFoundProblem,
            t.WORK_SOURCE_NOT_FOUND,
            "unknown work source 'nope'",
        ),
        (
            "get",
            "/api/work-sources/nope/items",
            "/api/work-sources/{source}/items",
            404,
            t.WorkSourceNotFoundProblem,
            t.WORK_SOURCE_NOT_FOUND,
            "unknown work source 'nope'",
        ),
        (
            "get",
            "/api/work-sources/hub/items/nope",
            "/api/work-sources/{source}/items/{ref}",
            404,
            t.WorkItemNotFoundProblem,
            t.WORK_ITEM_NOT_FOUND,
            None,
        ),
        (
            "get",
            "/api/graphs/nope",
            "/api/graphs/{graph_id}",
            404,
            t.GraphNotFoundProblem,
            t.GRAPH_NOT_FOUND,
            "unknown graph nope",
        ),
        (
            "get",
            "/api/config/schema/nope",
            "/api/config/schema/{kind}",
            404,
            t.ConfigSchemaNotFoundProblem,
            t.CONFIG_SCHEMA_NOT_FOUND,
            "no document schema for 'nope'",
        ),
    ):
        response = hub.client.get(path)
        if detail is None:
            detail = response.json()["detail"]
            assert "nope" in detail
        _refused(hub, response, method, template, status, model, type_, detail)


def test_a_query_naming_an_unknown_work_source_is_a_problem_locating_it(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    response = hub.client.get("/api/analytics/events", params={"source": "nope"})

    _refused(
        hub,
        response,
        "get",
        "/api/analytics/events",
        422,
        t.UnknownWorkSourceProblem,
        t.UNKNOWN_WORK_SOURCE,
        [{"loc": ["query", "source"], "msg": "unknown work source 'nope'", "type": "unknown_work_source"}],
    )


def test_a_project_body_naming_an_unknown_project_is_the_unknown_project_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    response = hub.client.post("/api/chunks", json={"tokens": ["hub:1"], "project": "nope"})

    body = response.json()
    assert (response.status_code, body["type"]) == (422, "urn:blizzard:problem:unknown-project")
    assert body["detail"] == [{"loc": ["body", "project"], "msg": "unknown project 'nope'", "type": "unknown_project"}]


def test_a_taken_or_stale_configuration_write_is_a_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, repositories=(), link=False)
    ensure_fixture_secret(hub.client)
    assert hub.client.post("/api/projects", json={"slug": "widgets", "name": "Widgets"}).status_code == 201
    taken = hub.client.post("/api/projects", json={"slug": "widgets", "name": "Again"})
    _refused(
        hub,
        taken,
        "post",
        "/api/projects",
        409,
        t.ProjectSlugTakenProblem,
        t.PROJECT_SLUG_TAKEN,
        "project slug widgets is already taken",
    )
    stale = hub.client.patch("/api/projects/widgets", json={"name": "W"}, headers={"If-Match": "9"})
    if stale.status_code == 409:
        _refused(
            hub,
            stale,
            "patch",
            "/api/projects/{project}",
            409,
            RevisionConflictProblem,
            REVISION_CONFLICT,
            stale.json()["detail"],
        )
    invalid = hub.client.post("/api/projects", json={"slug": "Not A Slug", "name": "x"})
    assert invalid.status_code == 422
    assert (
        declared_refusal_mismatches(_spec(hub), invalid, "post", "/api/projects", 422, t.ConfigFieldInvalidProblem)
        == []
    )
    assert invalid.json()["detail"][0]["loc"] == ["body", "slug"]

    forge = {"slug": "ghe", "kind": "github", "api_base": "https://ghe.example/api/v3"}
    assert hub.client.post("/api/forges", json=forge).status_code == 201
    again = hub.client.post("/api/forges", json={**forge, "api_base": "https://other.example/api/v3"})
    _refused(
        hub,
        again,
        "post",
        "/api/forges",
        409,
        t.ForgeSlugTakenProblem,
        t.FORGE_SLUG_TAKEN,
        {"code": "forge_slug_taken", "message": "forge ghe already exists", "slug": "ghe"},
    )
    stale_forge = hub.client.patch("/api/forges/ghe", json={}, headers={"If-Match": "9"})
    _refused(
        hub,
        stale_forge,
        "patch",
        "/api/forges/{forge}",
        409,
        t.ForgeRevisionConflictProblem,
        t.FORGE_REVISION_CONFLICT,
        {
            "code": "revision_conflict",
            "message": stale_forge.json()["detail"]["message"],
            "current": 1,
        },
    )


def test_a_refused_secret_write_is_a_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)

    badly_named = hub.client.post("/api/secrets", json={"name": "Bad Name", "value": "v"})
    assert badly_named.status_code == 422
    assert (
        declared_refusal_mismatches(_spec(hub), badly_named, "post", "/api/secrets", 422, t.SecretNameInvalidProblem)
        == []
    )
    assert badly_named.json()["type"] == t.SECRET_NAME_INVALID

    assert hub.client.post("/api/secrets", json={"name": "tok", "value": "v"}).status_code == 201
    assert hub.client.post("/api/secrets/tok/retire").status_code == 200
    retired = hub.client.put("/api/secrets/tok/value", json={"value": "w"})
    assert retired.status_code == 409
    assert (
        declared_refusal_mismatches(
            _spec(hub),
            retired,
            "put",
            "/api/secrets/{name}/value",
            409,
            t.SecretRetiredProblem,
        )
        == []
    )
    assert retired.json()["detail"].endswith("; enable it first")


def test_a_tenant_refusal_is_a_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default = default_tenant(hub.engine)
    headers = _hub_admin_headers(hub)

    only = hub.client.delete(f"/api/admin/tenants/{default}", headers=headers)
    _refused(
        hub,
        only,
        "delete",
        "/api/admin/tenants/{tenant_id}",
        409,
        t.LastTenantProblem,
        t.LAST_TENANT,
        {"code": "last_tenant", "message": "the hub's only open tenant cannot be deleted"},
    )
    nobody = hub.client.post(
        "/api/admin/tenants", json={"name": "acme", "admin_user_id": "usr_nobody"}, headers=headers
    )
    _refused(hub, nobody, "post", "/api/admin/tenants", 404, t.UserNotFoundProblem, t.USER_NOT_FOUND, "no such user")
    created = hub.client.post("/api/admin/tenants", json={"name": "acme"}, headers=headers).json()
    assert hub.client.delete(f"/api/admin/tenants/{created['tenant_id']}", headers=headers).status_code == 200
    gone = hub.client.delete(f"/api/admin/tenants/{created['tenant_id']}", headers=headers)
    _refused(
        hub,
        gone,
        "delete",
        "/api/admin/tenants/{tenant_id}",
        410,
        t.TenantDeletedProblem,
        t.TENANT_DELETED,
        "tenant deleted",
    )


def test_a_member_user_and_hub_administrator_refusal_is_a_problem(tmp_path: Path) -> None:
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    default_tenant(hub.engine)
    root = seed_user(hub, username="root", role=Role.SUPERUSER)
    admin = seed_user(hub, username="ada", role=Role.ADMIN)
    root_headers = {"Cookie": f"bz_session={seed_session(hub, root)}"}
    admin_headers = {"Cookie": f"bz_session={seed_session(hub, admin)}"}

    last = hub.client.delete(f"/api/admin/hub-admins/{root.user_id}", headers=root_headers)
    _refused(
        hub,
        last,
        "delete",
        "/api/admin/hub-admins/{user_id}",
        409,
        t.LastHubAdminProblem,
        t.LAST_HUB_ADMIN,
        last.json()["detail"],
    )
    missing = hub.client.put("/api/admin/hub-admins/usr_nobody", headers=root_headers)
    _refused(
        hub,
        missing,
        "put",
        "/api/admin/hub-admins/{user_id}",
        404,
        t.UserNotFoundProblem,
        t.USER_NOT_FOUND,
        "no such user",
    )

    unknown_role = hub.client.post(f"/api/users/{admin.user_id}/role", json={"role": "wizard"}, headers=admin_headers)
    _refused(
        hub,
        unknown_role,
        "post",
        "/api/users/{user_id}/role",
        400,
        t.UnknownRoleProblem,
        t.UNKNOWN_ROLE,
        "unknown role 'wizard'",
    )
    superuser = hub.client.post(f"/api/users/{admin.user_id}/role", json={"role": "superuser"}, headers=admin_headers)
    _refused(
        hub,
        superuser,
        "post",
        "/api/users/{user_id}/role",
        403,
        t.RoleNotAssignableProblem,
        t.ROLE_NOT_ASSIGNABLE,
        "superuser is not assignable through the API",
    )
    own = hub.client.put(f"/api/members/{admin.user_id}", json={"role": "guest"}, headers=admin_headers)
    _refused(
        hub,
        own,
        "put",
        "/api/members/{user_id}",
        403,
        t.MembershipChangeRefusedProblem,
        t.MEMBERSHIP_CHANGE_REFUSED,
        own.json()["detail"],
    )
    stranger = hub.client.delete("/api/members/usr_nobody", headers=admin_headers)
    _refused(
        hub,
        stranger,
        "delete",
        "/api/members/{user_id}",
        404,
        t.MemberNotFoundProblem,
        t.MEMBER_NOT_FOUND,
        "no such user",
    )

    nothing = hub.client.post("/api/invitations/inv_nope/revocations", headers=admin_headers)
    _refused(
        hub,
        nothing,
        "post",
        "/api/invitations/{invitation_id}/revocations",
        404,
        t.InvitationNotFoundProblem,
        t.INVITATION_NOT_FOUND,
        nothing.json()["detail"],
    )
