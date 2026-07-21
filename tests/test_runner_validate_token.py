"""``validate_federation_token`` — signature/``kid``, audience, expiry (±30s leeway),
and jti-replay checks (issue #95). Built against a locally-minted RSA keypair, mirroring
``tests/test_auth_oauth_providers.py``'s own JWKS-building convention — no network,
no dependency on the hub's own ``SigningKeyService``."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import httpx
import jwt
import pytest
from cryptography.hazmat.primitives.asymmetric import rsa
from jwt.algorithms import RSAAlgorithm

from blizzard.runner.auth.jwks_cache import JwksCache
from blizzard.runner.auth.validate import FederationTokenError, validate_federation_token

pytestmark = pytest.mark.unit

_KID = "test-kid"
_RUNNER_ID = "runner-a"


class _FakeJtiCache:
    def __init__(self) -> None:
        self.seen: set[str] = set()

    def check_and_record(self, jti: str, *, aud: str, expires_at: datetime) -> bool:
        del aud, expires_at
        if jti in self.seen:
            return False
        self.seen.add(jti)
        return True


def _keypair() -> tuple[object, dict[str, str]]:
    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    jwk = json.loads(RSAAlgorithm.to_jwk(private_key.public_key()))
    jwk["kid"] = _KID
    return private_key, jwk


def _jwks_cache(jwk: dict[str, str]) -> JwksCache:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json={"keys": [jwk]})

    client = httpx.Client(transport=httpx.MockTransport(handler), base_url="http://hub.example")
    return JwksCache(client, "/api/auth/jwks.json")


def _sign(private_key: object, *, claims: dict[str, object]) -> str:
    return jwt.encode(claims, private_key, algorithm="RS256", headers={"kid": _KID})  # type: ignore[arg-type]


def _claims(**overrides: object) -> dict[str, object]:
    now = datetime.now(UTC)
    base = {
        "sub": "usr_1",
        "username": "alice",
        "email": "alice@example.com",
        "role": "contributor",
        "aud": _RUNNER_ID,
        "jti": "jti-1",
        "iat": int(now.timestamp()),
        "exp": int((now + timedelta(seconds=60)).timestamp()),
    }
    base.update(overrides)
    return base


def test_a_valid_token_resolves_the_claimed_identity() -> None:
    private_key, jwk = _keypair()
    token = _sign(private_key, claims=_claims())
    identity = validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(jwk), jti_cache=_FakeJtiCache())
    assert identity.user_id == "usr_1"
    assert identity.username == "alice"
    assert identity.role == "contributor"


def test_a_token_minted_for_a_different_runner_is_rejected() -> None:
    private_key, jwk = _keypair()
    token = _sign(private_key, claims=_claims(aud="runner-b"))
    with pytest.raises(FederationTokenError):
        validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(jwk), jti_cache=_FakeJtiCache())


def test_a_token_signed_by_an_untrusted_key_is_rejected() -> None:
    _signing_key, _ = _keypair()
    _other_key, other_jwk = _keypair()  # the JWKS the runner fetches carries a DIFFERENT key under the same kid
    token = _sign(_signing_key, claims=_claims())
    with pytest.raises(FederationTokenError):
        validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(other_jwk), jti_cache=_FakeJtiCache())


def test_an_expired_token_past_the_leeway_is_rejected() -> None:
    private_key, jwk = _keypair()
    long_ago = datetime.now(UTC) - timedelta(minutes=5)
    token = _sign(
        private_key,
        claims=_claims(iat=int(long_ago.timestamp()), exp=int((long_ago + timedelta(seconds=60)).timestamp())),
    )
    with pytest.raises(FederationTokenError):
        validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(jwk), jti_cache=_FakeJtiCache())


def test_a_replayed_jti_is_rejected() -> None:
    private_key, jwk = _keypair()
    token = _sign(private_key, claims=_claims())
    jti_cache = _FakeJtiCache()
    validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(jwk), jti_cache=jti_cache)
    with pytest.raises(FederationTokenError):
        validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(jwk), jti_cache=jti_cache)


def test_an_unknown_kid_is_rejected() -> None:
    private_key, _jwk = _keypair()
    _, other_jwk = _keypair()  # the fetched JWKS names a different kid entirely
    other_jwk["kid"] = "some-other-kid"
    token = _sign(private_key, claims=_claims())
    with pytest.raises(FederationTokenError):
        validate_federation_token(token, runner_id=_RUNNER_ID, jwks=_jwks_cache(other_jwk), jti_cache=_FakeJtiCache())
