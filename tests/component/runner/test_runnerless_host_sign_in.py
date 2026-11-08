"""A host whose runners are all halted still signs the operator in as itself (component tier)."""

from __future__ import annotations

import contextlib
import dataclasses
import json
from collections.abc import Callable, Generator
from datetime import UTC, datetime, timedelta
from pathlib import Path
from urllib.parse import parse_qs, urlparse

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from fastapi.testclient import TestClient
from jwt.algorithms import RSAAlgorithm

import blizzard.runner.app as runner_app
from blizzard.foundation.host_registration_state import HostRegistrationState
from blizzard.runner.app import build_hosted_app
from blizzard.runner.auth.session import CookieNames
from blizzard.runner.composition import HostProcess, build_host_process
from blizzard.runner.config import RunnerConfig
from blizzard.runner.host_config import HaltedRunner, ResolvedToken, TokenSource
from blizzard.runner.host_layout import HostLayout
from blizzard.runner.hub.host_identity import HostIdentity
from blizzard.runner.hub.host_registration import HostRegistrationStatus
from tests.support.host_files import host_config_of
from tests.support.runner_fakes import migrated_store_at

_KID = "hub-kid-1"
_HOST_ID = "hs_01HOSTHOSTHOSTHOSTHOSTHOST"
_HALTED = (HaltedRunner(name="alpha", id=None, errors=("projects: none served",)),)


def _host(tmp_path: Path, *, token: str) -> tuple[HostProcess, RunnerConfig]:
    config = RunnerConfig(
        root=tmp_path,
        db_url=HostLayout(tmp_path).default_db_url,
        hub_url="http://hub.example",
        public_urls=("https://host.example",),
    )
    config.data_dir.mkdir(parents=True, exist_ok=True)
    migrated_store_at(config.db_url)
    host_config = dataclasses.replace(
        host_config_of(config), host_token=ResolvedToken(value=token, source=TokenSource.ENVIRONMENT)
    )
    return build_host_process(host_config, [], halted=_HALTED), config


Keys = tuple[rsa.RSAPrivateKey, dict[str, str]]


@pytest.fixture
def keypair() -> Keys:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = _KID
    return private_key, jwk


def _token(private_key: rsa.RSAPrivateKey, *, jti: str, aud: str) -> str:
    now = datetime.now(UTC)
    claims: dict[str, object] = {
        "sub": "usr_1",
        "username": "alice",
        "email": "alice@example.com",
        "role": "contributor",
        "aud": aud,
        "jti": jti,
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
    }
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})


@contextlib.contextmanager
def _serve(
    host: HostProcess, config: RunnerConfig, jwk: dict[str, str], monkeypatch: pytest.MonkeyPatch
) -> Generator[TestClient]:
    real: Callable[..., httpx2.Client] = httpx2.Client

    def handler(request: httpx2.Request) -> httpx2.Response:
        assert request.url.path == "/api/auth/jwks.json"
        return httpx2.Response(200, json={"keys": [jwk]})

    def mocked(**_kwargs: object) -> httpx2.Client:
        return real(transport=httpx2.MockTransport(handler), base_url="http://hub.example")

    monkeypatch.setattr(runner_app.httpx2, "Client", mocked)
    hosted = build_hosted_app(config, host)
    try:
        yield TestClient(hosted.app, base_url="https://host.example")
    finally:
        hosted.close()


def _answered(host: HostProcess) -> None:
    host.host_identity.hold(HostIdentity(host_id=_HOST_ID, tenant_id="tn_1", registered_at=datetime.now(UTC)))
    host.host_registration.hold(HostRegistrationStatus(state=HostRegistrationState.REGISTERED))


def test_a_registered_runnerless_host_signs_in_as_its_host_id(
    tmp_path: Path, keypair: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    private_key, jwk = keypair
    host, config = _host(tmp_path, token="host-token")
    _answered(host)
    with _serve(host, config, jwk, monkeypatch) as client:
        names = CookieNames(_HOST_ID)

        login = client.get("/api/auth/login?return_to=/", follow_redirects=False)
        assert login.status_code in (302, 307)
        assert parse_qs(urlparse(login.headers["location"]).query)["client"] == [_HOST_ID]
        state = login.cookies[names.bounce_state]
        client.cookies.set(names.bounce_state, state)
        client.cookies.set(names.bounce_return, "/")
        callback = client.post(
            "/api/auth/callback",
            content=f"token={_token(private_key, jti='j1', aud=_HOST_ID)}&state={state}",
            headers={"content-type": "application/x-www-form-urlencoded"},
            follow_redirects=False,
        )
        assert callback.status_code == 303
        client.cookies.set(names.session, callback.cookies[names.session])
        assert client.get("/api/host/environments").status_code == 200


@pytest.mark.parametrize(
    "state",
    [
        HostRegistrationState.PENDING,
        HostRegistrationState.REFUSED,
        HostRegistrationState.NO_HOSTS,
        HostRegistrationState.UNREACHABLE,
    ],
)
def test_a_runnerless_host_offers_no_sign_in_until_answered_as_itself(
    tmp_path: Path,
    keypair: Keys,
    monkeypatch: pytest.MonkeyPatch,
    state: HostRegistrationState,
) -> None:
    _key, jwk = keypair
    host, config = _host(tmp_path, token="host-token")
    _answered(host)
    host.host_registration.hold(HostRegistrationStatus(state=state))
    with _serve(host, config, jwk, monkeypatch) as client:
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 503


def test_a_runnerless_host_with_no_recorded_identity_offers_no_sign_in(
    tmp_path: Path, keypair: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key, jwk = keypair
    host, config = _host(tmp_path, token="host-token")
    host.host_registration.hold(HostRegistrationStatus(state=HostRegistrationState.REGISTERED))
    with _serve(host, config, jwk, monkeypatch) as client:
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 503


def test_an_unregistered_runnerless_host_offers_no_sign_in(
    tmp_path: Path, keypair: Keys, monkeypatch: pytest.MonkeyPatch
) -> None:
    _key, jwk = keypair
    host, config = _host(tmp_path, token="")
    with _serve(host, config, jwk, monkeypatch) as client:
        assert client.get("/api/auth/login", follow_redirects=False).status_code == 503
