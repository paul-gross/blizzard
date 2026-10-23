"""The ``oidc``/``github`` provider conformers — driven against a fake transport via
``httpx2.MockTransport`` (unit tier).

The whole authorize-url/exchange dance runs against canned responses, including a
real RSA signature verification of the ``oidc`` conformer's ``id_token`` — no network,
no running stub server."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
from datetime import UTC, datetime, timedelta

import httpx2
import jwt
import pytest
from cryptography.hazmat.primitives import serialization
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from blizzard.auth_core import Role
from blizzard.hub.auth.invitations import Invitation
from blizzard.hub.auth.oauth.internal.github_provider import GithubProvider
from blizzard.hub.auth.oauth.internal.oidc_provider import OidcProvider
from blizzard.hub.auth.oauth.provider import OAuthExchangeError
from blizzard.hub.tenancy import TenantId

pytestmark = pytest.mark.unit

_ISSUER = "https://issuer.example.com"
_CLIENT_ID = "cid-123"
_KID = "test-kid-1"


def _rsa_keypair() -> tuple[object, dict[str, object]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = _KID
    jwk["alg"] = "RS256"
    jwk["use"] = "sig"
    return private_key, jwk


def _id_token(private_key: object, *, claims: dict[str, object]) -> str:
    payload = {
        "iss": _ISSUER,
        "aud": _CLIENT_ID,
        "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
        **claims,
    }
    return jwt.encode(payload, private_key, algorithm="RS256", headers={"kid": _KID})  # type: ignore[arg-type]


def _oidc_transport(id_token_claims: dict[str, object] | None, *, token_status: int = 200):  # type: ignore[no-untyped-def]
    private_key, jwk = _rsa_keypair()
    token = _id_token(private_key, claims=id_token_claims) if id_token_claims is not None else None

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "authorization_endpoint": f"{_ISSUER}/authorize",
                    "token_endpoint": f"{_ISSUER}/token",
                    "jwks_uri": f"{_ISSUER}/jwks",
                },
            )
        if path == "/token":
            if token_status != 200:
                return httpx2.Response(token_status, json={"error": "invalid_grant"})
            return httpx2.Response(200, json={"id_token": token})
        if path == "/jwks":
            return httpx2.Response(200, json={"keys": [jwk]})
        return httpx2.Response(404)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _oidc_provider(client: httpx2.Client, *, emails_authoritative: bool = False) -> OidcProvider:
    return OidcProvider(
        name="oidc-co",
        display_name="Example SSO",
        issuer=_ISSUER,
        client_id=_CLIENT_ID,
        client_secret="s3cret",
        http_client=client,
        emails_authoritative=emails_authoritative,
    )


def test_oidc_authorize_url_carries_state_and_redirect_uri() -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1"}))
    url = provider.authorize_url(state="st1", redirect_uri="https://hub.test/api/auth/oidc-co/callback")
    assert url.startswith(f"{_ISSUER}/authorize?")
    assert "state=st1" in url
    assert "redirect_uri=https%3A%2F%2Fhub.test%2Fapi%2Fauth%2Foidc-co%2Fcallback" in url


def test_oidc_exchange_verifies_signature_and_resolves_identity() -> None:
    provider = _oidc_provider(
        _oidc_transport({"sub": "u1", "email": "ada@example.com", "email_verified": True, "preferred_username": "ada"})
    )
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.subject == "u1"
    assert identity.handle == "ada"
    assert identity.email == "ada@example.com"
    assert identity.email_verified is True


def test_oidc_exchange_respects_unverified_email() -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1", "email": "ada@example.com", "email_verified": False}))
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.email_verified is False
    assert identity.verified_emails == ()


@pytest.mark.parametrize("claim", ["false", "False", "no", "", 1, "1"])
def test_oidc_exchange_counts_only_a_true_claim_as_verified(claim: object) -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1", "email": "ada@example.com", "email_verified": claim}))
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.email_verified is False
    assert identity.verified_emails == ()


def test_oidc_exchange_treats_an_absent_claim_as_unverified() -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1", "email": "ada@example.com"}))
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.email_verified is False
    assert identity.verified_emails == ()


@pytest.mark.parametrize("claim", [True, "true", "TRUE", "True"])
def test_oidc_exchange_accepts_a_true_claim_as_boolean_or_string(claim: object) -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1", "email": "ada@example.com", "email_verified": claim}))
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.email_verified is True
    assert identity.verified_emails == ("ada@example.com",)


def _invitation_for(email: str) -> Invitation:
    issued = datetime(2026, 1, 1, tzinfo=UTC)
    return Invitation(
        invitation_id="inv_oidc",
        tenant=TenantId("ten_oidc"),
        email=email,
        role=Role.CONTRIBUTOR,
        token_hash="0" * 64,
        created_by="usr_admin",
        created_at=issued,
        expires_at=issued + timedelta(days=7),
    )


@pytest.mark.parametrize(("claim", "admitted"), [("false", False), ("true", True), (True, True)])
def test_an_authoritative_oidc_identity_accepts_an_invitation_only_with_a_verified_email(
    claim: object, admitted: bool
) -> None:
    claims = {"sub": "u1", "email": "ceo@corp.com", "email_verified": claim}
    provider = _oidc_provider(_oidc_transport(claims), emails_authoritative=True)
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert _invitation_for("ceo@corp.com").admits(identity) is admitted


def test_oidc_reports_its_verified_email_as_unauthoritative_unless_configured() -> None:
    claims = {"sub": "u1", "email": "ada@example.com", "email_verified": True}
    provider = _oidc_provider(_oidc_transport(claims))
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert provider.emails_authoritative is False
    assert identity.verified_emails == ("ada@example.com",)
    assert identity.emails_authoritative is False


def test_oidc_configured_authoritative_vouches_for_its_verified_email() -> None:
    claims = {"sub": "u1", "email": "ada@example.com", "email_verified": True}
    provider = _oidc_provider(_oidc_transport(claims), emails_authoritative=True)
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert provider.emails_authoritative is True
    assert identity.verified_emails == ("ada@example.com",)
    assert identity.emails_authoritative is True


def test_oidc_exchange_falls_back_to_subject_when_no_handle_claim() -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1"}))
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.handle == "u1"


def test_oidc_exchange_raises_on_a_rejected_code() -> None:
    provider = _oidc_provider(_oidc_transport({"sub": "u1"}, token_status=400))
    with pytest.raises(OAuthExchangeError):
        provider.exchange(code="bad", redirect_uri="https://hub.test/callback")


def test_oidc_exchange_raises_on_a_signature_that_does_not_verify() -> None:
    """A token signed by a key never published in the discovery JWKS must not verify."""
    other_private_key, _ = _rsa_keypair()
    forged = _id_token(other_private_key, claims={"sub": "attacker"})

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "authorization_endpoint": f"{_ISSUER}/authorize",
                    "token_endpoint": f"{_ISSUER}/token",
                    "jwks_uri": f"{_ISSUER}/jwks",
                },
            )
        if path == "/token":
            return httpx2.Response(200, json={"id_token": forged})
        if path == "/jwks":
            # A JWKS whose kid never matches the forged token's kid (`_KID`).
            _, real_jwk = _rsa_keypair()
            return httpx2.Response(200, json={"keys": [real_jwk]})
        return httpx2.Response(404)

    provider = _oidc_provider(httpx2.Client(transport=httpx2.MockTransport(handler)))
    with pytest.raises(OAuthExchangeError):
        provider.exchange(code="abc", redirect_uri="https://hub.test/callback")


def test_oidc_exchange_rejects_an_alg_confusion_token_when_jwk_omits_alg() -> None:
    """A JWKS entry with no ``alg`` member (legal per RFC 7517) must not let a token's
    ``HS256`` header pick the algorithm and HMAC-key off the published RSA public key
    (alg-confusion attack)."""
    _, jwk = _rsa_keypair()
    del jwk["alg"]
    public_pem = RSAAlgorithm.from_jwk(jwk).public_bytes(  # type: ignore[union-attr]
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    )
    # PyJWT's own ``encode`` refuses a PEM-shaped HMAC secret, so the forged token is
    # built by hand.
    header_b64 = base64.urlsafe_b64encode(json.dumps({"alg": "HS256", "typ": "JWT", "kid": _KID}).encode()).rstrip(b"=")
    payload_b64 = base64.urlsafe_b64encode(
        json.dumps(
            {
                "iss": _ISSUER,
                "aud": _CLIENT_ID,
                "sub": "attacker",
                "exp": int((datetime.now(UTC) + timedelta(minutes=5)).timestamp()),
            }
        ).encode()
    ).rstrip(b"=")
    signing_input = header_b64 + b"." + payload_b64
    signature = hmac.new(public_pem, signing_input, hashlib.sha256).digest()
    signature_b64 = base64.urlsafe_b64encode(signature).rstrip(b"=")
    forged = (signing_input + b"." + signature_b64).decode()

    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path.endswith("/.well-known/openid-configuration"):
            return httpx2.Response(
                200,
                json={
                    "authorization_endpoint": f"{_ISSUER}/authorize",
                    "token_endpoint": f"{_ISSUER}/token",
                    "jwks_uri": f"{_ISSUER}/jwks",
                },
            )
        if path == "/token":
            return httpx2.Response(200, json={"id_token": forged})
        if path == "/jwks":
            return httpx2.Response(200, json={"keys": [jwk]})
        return httpx2.Response(404)

    provider = _oidc_provider(httpx2.Client(transport=httpx2.MockTransport(handler)))
    with pytest.raises(OAuthExchangeError):
        provider.exchange(code="abc", redirect_uri="https://hub.test/callback")


def test_oidc_exchange_raises_when_id_token_carries_no_sub() -> None:
    provider = _oidc_provider(_oidc_transport({}))
    with pytest.raises(OAuthExchangeError):
        provider.exchange(code="abc", redirect_uri="https://hub.test/callback")


# --- github ------------------------------------------------------------------


def _github_transport(  # type: ignore[no-untyped-def]
    *, user: dict[str, object] | None, emails: list[dict[str, object]], token_status: int = 200
):
    def handler(request: httpx2.Request) -> httpx2.Response:
        path = request.url.path
        if path == "/login/oauth/access_token":
            if token_status != 200:
                return httpx2.Response(token_status, json={"error": "bad_verification_code"})
            return httpx2.Response(200, json={"access_token": "gho_token"})
        if path == "/user":
            if user is None:
                return httpx2.Response(404, json={"message": "not found"})
            return httpx2.Response(200, json=user)
        if path == "/user/emails":
            return httpx2.Response(200, json=emails)
        return httpx2.Response(404)

    return httpx2.Client(transport=httpx2.MockTransport(handler))


def _github_provider(client: httpx2.Client) -> GithubProvider:
    return GithubProvider(
        name="github", display_name="GitHub", client_id="cid", client_secret="secret", http_client=client
    )


def test_github_authorize_url_carries_state_and_scope() -> None:
    provider = _github_provider(_github_transport(user={"id": 1, "login": "octocat"}, emails=[]))
    url = provider.authorize_url(state="st1", redirect_uri="https://hub.test/callback")
    assert url.startswith("https://github.com/login/oauth/authorize?")
    assert "state=st1" in url
    assert "scope=user%3Aemail" in url


def test_github_exchange_resolves_numeric_id_as_subject() -> None:
    provider = _github_provider(
        _github_transport(
            user={"id": 42, "login": "octocat"},
            emails=[{"email": "octo@example.com", "primary": True, "verified": True}],
        )
    )
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.subject == "42"
    assert identity.handle == "octocat"
    assert identity.email == "octo@example.com"
    assert identity.email_verified is True


def test_github_exchange_picks_the_primary_email_among_several() -> None:
    provider = _github_provider(
        _github_transport(
            user={"id": 42, "login": "octocat"},
            emails=[
                {"email": "secondary@example.com", "primary": False, "verified": True},
                {"email": "primary@example.com", "primary": True, "verified": True},
            ],
        )
    )
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.email == "primary@example.com"


def test_github_exchange_reports_every_verified_email_as_authoritative() -> None:
    provider = _github_provider(
        _github_transport(
            user={"id": 42, "login": "octocat"},
            emails=[
                {"email": "secondary@example.com", "primary": False, "verified": True},
                {"email": "unproven@example.com", "primary": False, "verified": False},
                {"email": "primary@example.com", "primary": True, "verified": True},
            ],
        )
    )
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert provider.emails_authoritative is True
    assert identity.verified_emails == ("secondary@example.com", "primary@example.com")
    assert identity.emails_authoritative is True
    assert identity.email == "primary@example.com"


def test_github_exchange_respects_an_unverified_primary_email() -> None:
    provider = _github_provider(
        _github_transport(
            user={"id": 42, "login": "octocat"},
            emails=[{"email": "octo@example.com", "primary": True, "verified": False}],
        )
    )
    identity = provider.exchange(code="abc", redirect_uri="https://hub.test/callback")
    assert identity.email_verified is False
    assert identity.verified_emails == ()


def test_github_exchange_raises_on_a_rejected_code() -> None:
    provider = _github_provider(_github_transport(user=None, emails=[], token_status=400))
    with pytest.raises(OAuthExchangeError):
        provider.exchange(code="bad", redirect_uri="https://hub.test/callback")


def test_github_provider_api_base_override_points_both_authorize_and_api_calls() -> None:
    provider = GithubProvider(
        name="github",
        display_name="GitHub",
        client_id="cid",
        client_secret="secret",
        http_client=_github_transport(user={"id": 1, "login": "octocat"}, emails=[]),
        web_base="http://127.0.0.1:9",
        api_base="http://127.0.0.1:9",
    )
    url = provider.authorize_url(state="s", redirect_uri="https://hub.test/callback")
    assert url.startswith("http://127.0.0.1:9/login/oauth/authorize?")
