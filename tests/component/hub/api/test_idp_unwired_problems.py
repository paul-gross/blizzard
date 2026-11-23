"""The IdP's own disabled refusal must not hide an unavailable hub store."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.app import create_app
from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH, AuthConfig, HubConfig
from blizzard.wire.auth import IDP_DISABLED
from blizzard.wire.problem import HUB_STORE_UNAVAILABLE
from tests.support.flag_guard import flags_for


@pytest.mark.parametrize(
    ("method", "path"),
    [
        ("GET", "/api/auth/authorize?client=runner&redirect_uri=https://runner/callback&state=s"),
        ("GET", "/api/auth/jwks.json"),
        ("POST", "/api/auth/cli/token"),
    ],
)
def test_idp_routes_distinguish_unwired_store_from_disabled_idp(method: str, path: str) -> None:
    config = HubConfig(root=Path("."), db_url="sqlite://", auth=AuthConfig(mode=AUTH_MODE_OAUTH))
    app = create_app(config, flags=flags_for("hub"))  # store-free, exactly as the export/unit app is built
    client = TestClient(app)

    body = {"code": "c", "code_verifier": "v", "redirect_uri": "r"} if method == "POST" else None
    response = client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == HUB_STORE_UNAVAILABLE
    assert response.json()["instance"] == path.split("?")[0]

    app.state.config = replace(config, auth=AuthConfig(mode=AUTH_MODE_NONE))
    disabled = client.request(method, path, json=body)
    assert disabled.status_code == 404
    assert disabled.headers["content-type"] == "application/problem+json"
    assert disabled.json()["type"] == IDP_DISABLED
