"""The IdP's own disabled refusal must not hide an unavailable hub store."""

from dataclasses import replace
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.auth_core import Role
from blizzard.hub.app import create_app
from blizzard.hub.auth import wiring as auth_wiring
from blizzard.hub.auth.signing import SigningKeyService
from blizzard.hub.config import AUTH_MODE_NONE, AUTH_MODE_OAUTH, AuthConfig, HubConfig
from blizzard.wire.auth import IDP_DISABLED
from blizzard.wire.problem import HUB_STORE_UNAVAILABLE
from tests.support.scaffolding import build_hub, seed_session, seed_user


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
    app = create_app(config)
    client = TestClient(app)

    body = {"code": "c", "code_verifier": "v", "redirect_uri": "r"} if method == "POST" else None
    response = client.request(method, path, json=body)
    assert response.status_code == 503
    assert response.headers["content-type"] == "application/problem+json"
    assert response.json()["type"] == HUB_STORE_UNAVAILABLE
    assert response.json()["instance"] == path.split("?")[0]

    disabled_app = create_app(replace(config, auth=AuthConfig(mode=AUTH_MODE_NONE)))
    disabled = TestClient(disabled_app).request(method, path, json=body)
    assert disabled.status_code == 404
    assert disabled.headers["content-type"] == "application/problem+json"
    assert disabled.json()["type"] == IDP_DISABLED


def test_disabled_rotation_preempts_unavailable_auth_services() -> None:
    config = HubConfig(root=Path("."), db_url="sqlite://", auth=AuthConfig(mode=AUTH_MODE_NONE))
    client = TestClient(create_app(config))

    response = client.post("/api/auth/rotate-signing-key", headers={"Authorization": "Bearer unknown"})

    assert response.status_code == 404
    assert response.json() == {"detail": "the IdP surface is not enabled"}


def test_backed_hub_without_signing_keys_refuses_only_signing_paths(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    def no_signing_keys(config: HubConfig) -> SigningKeyService | None:
        return None

    monkeypatch.setattr(auth_wiring, "signing_keys", no_signing_keys)
    hub = build_hub(tmp_path, auth_mode=AUTH_MODE_OAUTH)
    assert hub.app is not None and hub.config is not None
    admin = seed_user(hub, username="admin", role=Role.SUPERUSER, email="admin@example.com")
    hub.client.cookies.set("bz_session", seed_session(hub, admin))
    authorize = hub.client.get(
        "/api/auth/authorize", params={"client": "runner", "redirect_uri": "https://runner/callback", "state": "s"}
    )
    jwks = hub.client.get("/api/auth/jwks.json")
    rotate = hub.client.post("/api/auth/rotate-signing-key")
    exchange = hub.client.post("/api/auth/cli/token", json={"code": "c", "code_verifier": "v", "redirect_uri": "r"})

    assert authorize.status_code == jwks.status_code == rotate.status_code == 404
    assert authorize.json()["type"] == jwks.json()["type"] == IDP_DISABLED
    assert rotate.json() == {"detail": "the IdP surface is not enabled"}
    assert exchange.status_code == 400
