"""Verify a hub-signed federation JWT (issue #95).

The runner-side counterpart to ``hub/api/idp.py``'s ``authorize``: signature
verification (``kid``-selected against the fetched+cached hub JWKS, refetch on unknown
``kid`` — :class:`~blizzard.runner.auth.jwks_cache.JwksCache`), ``aud == this
runner_id``, ``exp`` honored with ±30s clock-skew leeway (PyJWT's own ``leeway``
covers this uniformly), and a replayed ``jti`` rejected via the store-backed single-use
cache (decision D4, :class:`~blizzard.runner.auth.jti_cache.IJtiCache`). Every failure
mode collapses to one :class:`FederationTokenError` — the callback route
(``runner/auth/federation.py``) treats "bad signature", "wrong audience", "expired",
and "replayed" identically (a refused bounce), never leaking which.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime

import jwt

from blizzard.runner.auth.jti_cache import IJtiCache
from blizzard.runner.auth.jwks_cache import JwksCache

#: The ±30s clock-skew leeway the issue's own AC names, applied uniformly to `exp`
#: (and `iat`, harmlessly) by PyJWT's own `leeway` kwarg.
CLOCK_SKEW_LEEWAY_SECONDS = 30


class FederationTokenError(Exception):
    """A presented federation token failed validation — bad signature, wrong
    audience, expired (past the leeway), malformed, or a replayed ``jti``."""


@dataclass(frozen=True)
class FederatedIdentity:
    """The claims a validated federation token resolves to — what
    ``runner/auth/roles.py`` resolves a local role from."""

    user_id: str
    username: str
    email: str | None
    role: str


def validate_federation_token(
    token: str, *, runner_id: str, jwks: JwksCache, jti_cache: IJtiCache
) -> FederatedIdentity:
    try:
        header = jwt.get_unverified_header(token)
    except jwt.PyJWTError as exc:
        raise FederationTokenError(f"malformed token: {exc}") from exc
    kid = header.get("kid")
    key = jwks.key_for(kid) if kid else None
    if key is None:
        raise FederationTokenError(f"no JWKS key matches kid {kid!r}")
    try:
        claims = jwt.decode(
            token,
            key=key,  # type: ignore[arg-type]
            algorithms=["RS256"],
            audience=runner_id,
            leeway=CLOCK_SKEW_LEEWAY_SECONDS,
        )
    except jwt.PyJWTError as exc:
        raise FederationTokenError(f"token invalid: {exc}") from exc

    jti = claims.get("jti")
    sub = claims.get("sub")
    username = claims.get("username")
    role = claims.get("role")
    if not jti or not sub or not username or not role:
        raise FederationTokenError("token is missing a required claim")

    exp = claims.get("exp")
    expires_at = datetime.fromtimestamp(exp, tz=UTC) if exp is not None else datetime.now(UTC)
    if not jti_cache.check_and_record(jti, aud=runner_id, expires_at=expires_at):
        raise FederationTokenError(f"jti {jti!r} already used (replay)")

    return FederatedIdentity(user_id=str(sub), username=str(username), email=claims.get("email"), role=str(role))
