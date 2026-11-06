"""The IdP's own disabled refusal must not hide an unavailable hub store."""

from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from starlette.exceptions import HTTPException

from blizzard.hub.api.idp import router
from blizzard.hub.api.problem_exceptions import problem_http_exception
from blizzard.wire.problem import HUB_STORE_UNAVAILABLE, IDP_DISABLED


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/auth/authorize?client=runner&redirect_uri=https://runner/callback&state=s"),
        ("GET", "/api/auth/jwks.json"),
        ("POST", "/api/auth/cli/token"),
    ],
)
def test_idp_routes_distinguish_unwired_store_from_disabled_idp(method: str, path: str) -> None:
    app = FastAPI()
    app.state.config = SimpleNamespace(auth=SimpleNamespace(mode="oauth"))
    app.add_exception_handler(HTTPException, problem_http_exception)
    app.include_router(router)
    client = TestClient(app)

    body = {"code": "c", "code_verifier": "v", "redirect_uri": "r"} if method == "POST" else None
    response = client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == HUB_STORE_UNAVAILABLE
    assert response.json()["instance"] == path.split("?")[0]

    app.state.config.auth.mode = "none"
    disabled = client.request(method, path, json=body)
    assert disabled.status_code == 404
    assert disabled.headers["content-type"] == "application/problem+json"
    assert disabled.json()["type"] == IDP_DISABLED
