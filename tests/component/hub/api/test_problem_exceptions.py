"""A typed refusal renders as its problem only where the operation declares it; every other refusal keeps
FastAPI's default body."""

from functools import partial

import pytest
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException as StarletteHTTPException

from blizzard.hub.api import problems
from blizzard.hub.api.problem_exceptions import ProblemFactory, ProblemHTTPException, problem_http_exception
from blizzard.hub.auth.permissions import Permission
from blizzard.wire import problem as problem_types
from blizzard.wire.problem import AUTHENTICATION_REQUIRED, CHUNK_NOT_FOUND


def _app() -> FastAPI:
    app = FastAPI()
    app.add_exception_handler(StarletteHTTPException, problem_http_exception)
    return app


def test_unknown_query_project_is_scoped_and_preserves_detail() -> None:
    app = _app()

    @app.get("/api/fleet/queue/peek")
    @app.get("/unrelated")
    def refuse(project: str, count: int = 1) -> None:
        raise ProblemHTTPException(partial(problems.unknown_query_project_problem, project))

    client = TestClient(app)
    expected = [{"loc": ["query", "project"], "msg": "unknown project 'missing'", "type": "unknown_project"}]
    response = client.get("/api/fleet/queue/peek?project=missing")
    assert response.status_code == 422
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == problem_types.UNKNOWN_PROJECT
    assert response.json()["detail"] == expected
    assert client.get("/unrelated?project=missing").json() == {"detail": expected}
    assert client.get("/api/fleet/queue/peek?project=missing&count=bad").headers["content-type"] == "application/json"


def test_declared_refusals_and_default_responses() -> None:
    app = _app()

    @app.get("/api/fleet/chunks/{chunk_id}")
    def chunk(chunk_id: str, mode: int = 0) -> None:
        if mode == 0:
            raise ProblemHTTPException(
                partial(problems.chunk_not_found_problem, chunk_id), headers={"X-Test": "preserved"}
            )
        if mode == 1:
            raise ProblemHTTPException(
                partial(problems.authentication_required_problem, detail="missing or malformed Authorization header")
            )
        raise HTTPException(404, "unrecognized")

    @app.get("/unrelated/{chunk_id}")
    def unrelated(chunk_id: str) -> None:
        raise ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))

    client = TestClient(app)
    response = client.get("/api/fleet/chunks/absent?secret=hidden")
    assert response.status_code == 404
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-test"] == "preserved"
    assert response.json()["type"] == CHUNK_NOT_FOUND
    assert response.json()["instance"] == "/api/fleet/chunks/absent"
    assert client.get("/api/fleet/chunks/absent?mode=1").json()["type"] == AUTHENTICATION_REQUIRED
    assert client.get("/api/fleet/chunks/absent?mode=2").json() == {"detail": "unrecognized"}
    assert client.get("/unrelated/absent").json() == {"detail": "unknown chunk absent"}
    assert client.get("/api/fleet/chunks/absent?mode=invalid").status_code == 422
    assert client.get("/missing").json() == {"detail": "Not Found"}


def test_untyped_refusal_with_a_declared_variants_wording_keeps_the_default_body() -> None:
    app = _app()

    @app.get("/api/fleet/chunks/{chunk_id}")
    def chunk(chunk_id: str) -> None:
        raise HTTPException(404, f"unknown chunk {chunk_id}")

    response = TestClient(app).get("/api/fleet/chunks/absent")
    assert response.headers["content-type"] == "application/json"
    assert response.json() == {"detail": "unknown chunk absent"}


@pytest.mark.parametrize(
    ("problem", "status", "detail", "problem_type"),
    [
        (
            partial(problems.authentication_required_problem, detail="authentication required"),
            401,
            "authentication required",
            problem_types.AUTHENTICATION_REQUIRED,
        ),
        (
            problems.tenant_required_problem,
            409,
            {"code": "tenant_required", "message": "name the tenant to act in with X-Blizzard-Tenant"},
            problem_types.TENANT_REQUIRED,
        ),
        (
            problems.machine_credential_forbidden_problem,
            403,
            "a machine credential is not valid here",
            problem_types.MACHINE_CREDENTIAL_FORBIDDEN,
        ),
        (
            partial(problems.permission_required_problem, Permission.RUNNER_RETIRE),
            403,
            f"missing permission {Permission.RUNNER_RETIRE!r}",
            problem_types.PERMISSION_REQUIRED,
        ),
        (
            partial(problems.runner_credential_forbidden_problem, "runner-1"),
            403,
            "runner token for 'runner-1' is not valid on an operator verb",
            problem_types.RUNNER_CREDENTIAL_FORBIDDEN,
        ),
    ],
)
def test_bootstrap_dependency_refusals(problem: ProblemFactory, status: int, detail: object, problem_type: str) -> None:
    app = _app()

    @app.post("/api/hosts")
    @app.post("/unrelated")
    def refuse() -> None:
        raise ProblemHTTPException(problem, headers={"X-Test": "preserved", "WWW-Authenticate": "Bearer"})

    client = TestClient(app)
    response = client.post("/api/hosts?secret=hidden")
    assert response.status_code == status
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["x-test"] == "preserved"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["type"] == problem_type
    assert response.json()["detail"] == detail
    assert response.json()["instance"] == "/api/hosts"
    unrelated = client.post("/unrelated")
    assert unrelated.status_code == status
    assert unrelated.json() == {"detail": detail}
    assert unrelated.headers["x-test"] == "preserved"


def test_login_refusals_preserve_headers_and_ignore_other_routes() -> None:
    app = _app()

    @app.get("/api/auth/{name}/callback")
    @app.get("/other/{name}/callback")
    def refused(name: str) -> None:
        raise ProblemHTTPException(problems.login_throttled_problem, headers={"Retry-After": "30"})

    client = TestClient(app)
    response = client.get("/api/auth/github/callback?state=secret")
    assert response.headers["retry-after"] == "30"
    assert response.json()["type"] == problem_types.LOGIN_THROTTLED
    assert response.json()["instance"] == "/api/auth/github/callback"
    unrelated = client.get("/other/github/callback")
    assert unrelated.headers["retry-after"] == "30"
    assert unrelated.json() == {"detail": "too many login attempts — try again shortly"}


def test_cli_token_store_unavailable_is_declared_and_typed() -> None:
    app = _app()

    @app.post("/api/auth/cli/token")
    @app.post("/other/token")
    def unavailable() -> None:
        raise ProblemHTTPException(problems.hub_store_unavailable_problem, headers={"Retry-After": "5"})

    client = TestClient(app)
    response = client.post("/api/auth/cli/token?code=secret")
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["retry-after"] == "5"
    assert response.json()["type"] == problem_types.HUB_STORE_UNAVAILABLE
    assert response.json()["instance"] == "/api/auth/cli/token"
    assert client.post("/other/token").json() == {"detail": "hub store not wired — start via `blizzard hub host`"}


@pytest.mark.parametrize("path", ["/api/fleet/queue/peek", "/api/fleet/chunks/c1/pm-items"])
def test_fleet_gets_declare_dependency_refusals(path: str) -> None:
    app = _app()

    @app.get("/api/fleet/queue/peek")
    @app.get("/api/fleet/chunks/{chunk_id}/pm-items")
    def refused() -> None:
        raise ProblemHTTPException(
            partial(problems.authentication_required_problem, detail="missing or malformed Authorization header"),
            headers={"WWW-Authenticate": "Bearer"},
        )

    response = TestClient(app).get(path)
    assert response.status_code == 401
    assert response.headers["content-type"] == "application/problem+json"
    assert response.headers["www-authenticate"] == "Bearer"
    assert response.json()["type"] == AUTHENTICATION_REQUIRED
    assert response.json()["detail"] == "missing or malformed Authorization header"


def test_pm_items_unknown_chunk_is_typed() -> None:
    app = _app()

    @app.get("/api/fleet/chunks/{chunk_id}/pm-items")
    def refused(chunk_id: str) -> None:
        raise ProblemHTTPException(partial(problems.chunk_not_found_problem, chunk_id))

    response = TestClient(app).get("/api/fleet/chunks/c1/pm-items")
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == CHUNK_NOT_FOUND
    assert response.json()["detail"] == "unknown chunk c1"
