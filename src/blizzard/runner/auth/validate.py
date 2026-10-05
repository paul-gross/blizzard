"""Verify a hub-signed federation JWT.

``kid``-selected signature verification against the cached hub JWKS, ``aud == this runner_id``, a
required ``exp`` with ±30s leeway (:meth:`FederatedIdentity.from_claims`), and a replayed ``jti``
refused via the store-backed single-use cache. Every failure collapses to one :class:`FederationTokenError`."""

from __future__ import annotations

from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

from blizzard.foundation.clock import IClock
from blizzard.foundation.roles import domain_model
from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import IJwksCache

#: The ±30s clock-skew leeway: applied to ``exp`` by :meth:`FederatedIdentity.from_claims`, and to
#: ``iat``, harmlessly, by PyJWT's own ``leeway`` kwarg.
CLOCK_SKEW_LEEWAY_SECONDS = 30

#: The claims a federation token must carry; ``exp`` too, else its ``jti`` could replay unretained.
REQUIRED_CLAIMS = ("jti", "sub", "username", "role", "exp")


class FederationTokenError(Exception):
    """A presented federation token failed validation — bad signature, wrong
    audience, expired (past the leeway), malformed, or a replayed ``jti``."""


@domain_model
@dataclass(frozen=True)
class FederatedIdentity:
    """The claims a validated federation token resolves to — what
    ``runner/auth/roles.py`` resolves a local role from."""

    user_id: str
    username: str
    email: str | None
    role: str

    @classmethod
    def from_claims(cls, claims: Mapping[str, object], *, now: datetime) -> FederatedIdentity:
        """The identity a signature-verified token's ``claims`` carry at ``now`` — raising
        :class:`FederationTokenError` when a required claim is missing or the token expired
        past the leeway."""
        if any(not claims.get(name) for name in REQUIRED_CLAIMS):
            raise FederationTokenError("token is missing a required claim")
        if jti_retention(claims) + timedelta(seconds=CLOCK_SKEW_LEEWAY_SECONDS) <= now:
            raise FederationTokenError("token expired")
        email = claims.get("email")
        return cls(
            user_id=str(claims["sub"]),
            username=str(claims["username"]),
            email=str(email) if email is not None else None,
            role=str(claims["role"]),
        )


def jti_retention(claims: Mapping[str, object]) -> datetime:
    """Until when a token's ``jti`` is remembered against replay: its own ``exp`` — raising
    :class:`FederationTokenError` when that is not a timestamp."""
    exp = claims.get("exp")
    if isinstance(exp, bool) or not isinstance(exp, int | float):
        raise FederationTokenError("token expiry is not a timestamp")
    return datetime.fromtimestamp(exp, tz=UTC)


@dataclass(frozen=True)
class FederationToken:
    """One presented federation token, and everything it is judged against."""

    raw: str
    runner_id: str
    jwks: IJwksCache
    jti_cache: IJtiCache
    clock: IClock

    def identity(self) -> FederatedIdentity:
        """The claims, once signature, audience, expiry and single-use all hold —
        raising :class:`FederationTokenError` otherwise."""
        try:
            header = jwt.get_unverified_header(self.raw)
        except jwt.PyJWTError as exc:
            raise FederationTokenError(f"malformed token: {exc}") from exc
        kid = header.get("kid")
        key = self.jwks.key_for(kid) if kid else None
        if key is None:
            raise FederationTokenError(f"no JWKS key matches kid {kid!r}")
        try:
            claims = jwt.decode(
                self.raw,
                key=key,  # type: ignore[arg-type]
                algorithms=["RS256"],
                audience=self.runner_id,
                leeway=CLOCK_SKEW_LEEWAY_SECONDS,
                # Expiry is judged against the injected clock by the model, not PyJWT's wall clock.
                options={"verify_exp": False},
            )
        except jwt.PyJWTError as exc:
            raise FederationTokenError(f"token invalid: {exc}") from exc

        identity = FederatedIdentity.from_claims(claims, now=self.clock.now())
        jti = str(claims["jti"])
        if not self.jti_cache.check_and_record(jti, aud=self.runner_id, expires_at=jti_retention(claims)):
            raise FederationTokenError(f"jti {jti!r} already used (replay)")
        return identity
