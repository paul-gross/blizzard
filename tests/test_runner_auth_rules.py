"""Runner-local human-auth rules (unit tier, by value): which identity a request resolves to, the
claims a federation token must carry, its expiry against the injected instant, and how long its
``jti`` is remembered."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.auth_core import Role
from blizzard.runner.auth.session import IMPLICIT_SESSION, RunnerSession, resolve_human_session
from blizzard.runner.auth.validate import (
    CLOCK_SKEW_LEEWAY_SECONDS,
    FederatedIdentity,
    FederationTokenError,
    jti_retention,
)

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 4, 12, 0, tzinfo=UTC)
_ALICE = RunnerSession(username="alice", role=Role.CONTRIBUTOR, issued_at=_NOW, expires_at=_NOW + timedelta(hours=8))


def _unasked() -> bool:
    raise AssertionError("asked although the answer did not need it")


def _unasked_session() -> RunnerSession | None:
    raise AssertionError("asked although the answer did not need it")


def test_socket_peer_is_superuser() -> None:
    session = resolve_human_session(socket_peer=True, gated=_unasked, presented=_unasked_session)
    assert session == IMPLICIT_SESSION
    assert IMPLICIT_SESSION.role is Role.SUPERUSER


def test_ungated_is_superuser() -> None:
    assert resolve_human_session(socket_peer=False, gated=lambda: False, presented=_unasked_session) == IMPLICIT_SESSION


def test_gated_uses_presented() -> None:
    assert resolve_human_session(socket_peer=False, gated=lambda: True, presented=lambda: _ALICE) == _ALICE
    assert resolve_human_session(socket_peer=False, gated=lambda: True, presented=lambda: None) is None


def _claims(**overrides: object) -> dict[str, object]:
    claims: dict[str, object] = {
        "jti": "jti-1",
        "sub": "usr_1",
        "username": "alice",
        "email": "alice@example.com",
        "role": "contributor",
        "exp": int((_NOW + timedelta(seconds=60)).timestamp()),
    }
    claims.update(overrides)
    return {name: value for name, value in claims.items() if value is not None}


def test_valid_claims_resolve_the_identity() -> None:
    assert FederatedIdentity.from_claims(_claims(), now=_NOW) == FederatedIdentity(
        user_id="usr_1", username="alice", email="alice@example.com", role="contributor"
    )


@pytest.mark.parametrize("missing", ["jti", "sub", "username", "role"])
def test_missing_claim_refused(missing: str) -> None:
    with pytest.raises(FederationTokenError, match="missing a required claim"):
        FederatedIdentity.from_claims(_claims(**{missing: None}), now=_NOW)


def test_token_without_exp_refused() -> None:
    with pytest.raises(FederationTokenError, match="missing a required claim"):
        FederatedIdentity.from_claims(_claims(exp=None), now=_NOW)


def test_non_numeric_exp_refused() -> None:
    with pytest.raises(FederationTokenError):
        FederatedIdentity.from_claims(_claims(exp="tomorrow"), now=_NOW)


def test_expired_past_leeway_refused_at_injected_now() -> None:
    exp = _NOW - timedelta(seconds=CLOCK_SKEW_LEEWAY_SECONDS)
    claims = _claims(exp=int(exp.timestamp()))
    with pytest.raises(FederationTokenError, match="expired"):
        FederatedIdentity.from_claims(claims, now=_NOW)
    assert FederatedIdentity.from_claims(claims, now=_NOW - timedelta(seconds=1)).username == "alice"


def test_jti_retained_until_exp_plus_leeway() -> None:
    exp = _NOW + timedelta(minutes=5)
    assert jti_retention(_claims(exp=int(exp.timestamp()))) == exp + timedelta(seconds=30)
