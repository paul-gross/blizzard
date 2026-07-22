"""The hub's provider-login plane behind a trusted reverse proxy (component tier, #130).

Drives the wired hub over a second ``TestClient`` bound to a concrete peer IP (the
default ``testclient`` is not an IP and so can never match a CIDR) with
``X-Forwarded-*`` headers set, asserting the three proxy-aware decisions: the cookie
``Secure`` flag, the throttle key, and the ``login_failed`` fact actor — each honored
only from a listed proxy, ignored from any other peer.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from blizzard.hub.auth.models import ProviderIdentity
from tests.support import build_hub
from tests.test_auth_login_api import FakeOAuthProvider, _state_from_redirect

pytestmark = pytest.mark.component

_PROXY_IP = "10.0.0.4"
_DIRECT_IP = "203.0.113.9"


def _client(hub, peer: str) -> TestClient:  # type: ignore[no-untyped-def]
    assert hub.app is not None
    return TestClient(hub.app, client=(peer, 41000))


# --- cookie Secure flag -----------------------------------------------------------


def test_forwarded_proto_https_from_a_trusted_proxy_mints_a_secure_cookie(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    github.codes["c1"] = ProviderIdentity(subject="1", handle="ada", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}, trusted_proxies=[_PROXY_IP])
    client = _client(hub, _PROXY_IP)

    headers = {"x-forwarded-proto": "https"}
    authorize = client.get("/api/auth/github/authorize", headers=headers, follow_redirects=False)
    state = _state_from_redirect(authorize.headers["location"])
    callback = client.get(
        f"/api/auth/github/callback?code=c1&state={state}", headers=headers, follow_redirects=False
    )
    assert callback.status_code in (302, 307)
    set_cookie = callback.headers["set-cookie"]
    assert "bz_session=" in set_cookie
    assert "Secure" in set_cookie


def test_forwarded_proto_https_from_an_unlisted_peer_is_ignored(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    github.codes["c1"] = ProviderIdentity(subject="1", handle="ada", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}, trusted_proxies=[_PROXY_IP])
    client = _client(hub, _DIRECT_IP)  # not the configured proxy

    headers = {"x-forwarded-proto": "https"}
    authorize = client.get("/api/auth/github/authorize", headers=headers, follow_redirects=False)
    state = _state_from_redirect(authorize.headers["location"])
    callback = client.get(
        f"/api/auth/github/callback?code=c1&state={state}", headers=headers, follow_redirects=False
    )
    assert callback.status_code in (302, 307)
    assert "Secure" not in callback.headers["set-cookie"]


def test_with_no_trusted_proxies_the_forwarded_proto_header_is_ignored(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    github.codes["c1"] = ProviderIdentity(subject="1", handle="ada", email=None, email_verified=False)
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github})  # empty default
    client = _client(hub, _PROXY_IP)

    headers = {"x-forwarded-proto": "https"}
    authorize = client.get("/api/auth/github/authorize", headers=headers, follow_redirects=False)
    state = _state_from_redirect(authorize.headers["location"])
    callback = client.get(
        f"/api/auth/github/callback?code=c1&state={state}", headers=headers, follow_redirects=False
    )
    assert "Secure" not in callback.headers["set-cookie"]


# --- throttle keying --------------------------------------------------------------


def test_throttle_keys_on_the_forwarded_client_ip_from_a_trusted_proxy(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}, trusted_proxies=[_PROXY_IP])
    client = _client(hub, _PROXY_IP)

    # One forwarded client hammers the endpoint past the bucket capacity...
    noisy = [
        client.get("/api/auth/github/authorize", headers={"x-forwarded-for": "198.51.100.7"}).status_code
        for _ in range(15)
    ]
    assert 429 in noisy
    # ...while a different forwarded client behind the same proxy is unaffected.
    quiet = client.get("/api/auth/github/authorize", headers={"x-forwarded-for": "198.51.100.8"})
    assert quiet.status_code != 429


def test_a_forged_forwarded_for_from_an_untrusted_peer_cannot_dodge_the_throttle(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}, trusted_proxies=[_PROXY_IP])
    client = _client(hub, _DIRECT_IP)  # untrusted direct peer

    # Rotating a forged X-Forwarded-For does not create fresh buckets — every request
    # keys on the untrusted direct peer, so the throttle still trips.
    statuses = [
        client.get("/api/auth/github/authorize", headers={"x-forwarded-for": f"1.2.3.{i}"}).status_code
        for i in range(15)
    ]
    assert 429 in statuses


# --- fact actor -------------------------------------------------------------------


def test_login_failed_fact_records_the_forwarded_client_ip(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}, trusted_proxies=[_PROXY_IP])
    client = _client(hub, _PROXY_IP)

    resp = client.get(
        "/api/auth/github/callback?code=abc&state=never-minted",
        headers={"x-forwarded-for": "198.51.100.7"},
        follow_redirects=False,
    )
    assert resp.status_code == 400
    facts = hub.services.auth_facts.list_recent()
    failed = [f for f in facts if f.kind == "login_failed"]
    assert failed and failed[0].actor == "198.51.100.7"


def test_login_failed_fact_records_the_direct_peer_for_an_untrusted_client(tmp_path: Path) -> None:
    github = FakeOAuthProvider(name="github")
    hub = build_hub(tmp_path, auth_mode="oauth", oauth_providers={"github": github}, trusted_proxies=[_PROXY_IP])
    client = _client(hub, _DIRECT_IP)

    resp = client.get(
        "/api/auth/github/callback?code=abc&state=never-minted",
        headers={"x-forwarded-for": "198.51.100.7"},  # forged — ignored
        follow_redirects=False,
    )
    assert resp.status_code == 400
    facts = hub.services.auth_facts.list_recent()
    failed = [f for f in facts if f.kind == "login_failed"]
    assert failed and failed[0].actor == _DIRECT_IP
