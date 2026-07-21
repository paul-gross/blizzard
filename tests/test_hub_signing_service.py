"""``SigningKeyService`` — keypair lifecycle, sign/verify round-trip, JWKS shape,
rotation, and on-disk permissions (issue #95)."""

from __future__ import annotations

import stat
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import cast

import jwt
import pytest
from jwt.algorithms import RSAAlgorithm

from blizzard.hub.auth.signing import SigningKeyService

pytestmark = pytest.mark.unit


def _jwks_keys(jwks: dict[str, object]) -> list[dict[str, str]]:
    return cast(list[dict[str, str]], jwks["keys"])


def test_sign_and_verify_round_trip(tmp_path: Path) -> None:
    service = SigningKeyService(tmp_path / "signing-keys")
    now = datetime(2026, 7, 21, tzinfo=UTC)
    token = service.sign({"sub": "u1", "aud": "runner-a"}, now=now, ttl=timedelta(seconds=60))

    header = jwt.get_unverified_header(token)
    kid = header["kid"]
    jwks = service.public_jwks()
    matching = [k for k in _jwks_keys(jwks) if k["kid"] == kid]
    assert len(matching) == 1
    public_key = RSAAlgorithm.from_jwk(matching[0])  # type: ignore[arg-type]
    claims = jwt.decode(
        token,
        key=public_key,  # type: ignore[arg-type]
        algorithms=["RS256"],
        audience="runner-a",
        options={"verify_exp": False},
    )
    assert claims["sub"] == "u1"
    assert claims["exp"] - claims["iat"] == 60


def test_keys_persist_across_instances(tmp_path: Path) -> None:
    keys_dir = tmp_path / "signing-keys"
    first = SigningKeyService(keys_dir)
    second = SigningKeyService(keys_dir)
    assert first.public_jwks() == second.public_jwks()


def test_jwks_starts_with_one_key(tmp_path: Path) -> None:
    service = SigningKeyService(tmp_path / "signing-keys")
    jwks = service.public_jwks()
    assert len(_jwks_keys(jwks)) == 1


def test_rotate_publishes_current_and_previous(tmp_path: Path) -> None:
    service = SigningKeyService(tmp_path / "signing-keys")
    before = {k["kid"] for k in _jwks_keys(service.public_jwks())}
    service.rotate()
    after = _jwks_keys(service.public_jwks())
    after_kids = {k["kid"] for k in after}
    assert len(after) == 2
    assert before <= after_kids  # the old current is now published as previous


def test_rotate_drops_the_oldest_generation(tmp_path: Path) -> None:
    service = SigningKeyService(tmp_path / "signing-keys")
    gen1 = {k["kid"] for k in _jwks_keys(service.public_jwks())}
    service.rotate()
    gen2 = {k["kid"] for k in _jwks_keys(service.public_jwks())}
    service.rotate()
    gen3 = {k["kid"] for k in _jwks_keys(service.public_jwks())}
    assert len(gen3) == 2
    # gen1's single key is now two generations old — dropped.
    assert not gen1 & gen3
    assert gen2 & gen3  # gen2's current became gen3's previous


def test_token_signed_before_rotation_still_verifies_against_published_jwks(tmp_path: Path) -> None:
    service = SigningKeyService(tmp_path / "signing-keys")
    now = datetime(2026, 7, 21, tzinfo=UTC)
    token = service.sign({"sub": "u1", "aud": "runner-a"}, now=now, ttl=timedelta(seconds=60))
    service.rotate()

    header = jwt.get_unverified_header(token)
    matching = [k for k in _jwks_keys(service.public_jwks()) if k["kid"] == header["kid"]]
    assert len(matching) == 1  # still published as "previous"
    public_key = RSAAlgorithm.from_jwk(matching[0])  # type: ignore[arg-type]
    jwt.decode(
        token,
        key=public_key,  # type: ignore[arg-type]
        algorithms=["RS256"],
        audience="runner-a",
        options={"verify_exp": False},
    )


def test_keys_dir_and_files_are_owner_only(tmp_path: Path) -> None:
    keys_dir = tmp_path / "signing-keys"
    SigningKeyService(keys_dir)
    dir_mode = stat.S_IMODE(keys_dir.stat().st_mode)
    assert dir_mode == 0o700
    pem_files = list(keys_dir.glob("*.pem"))
    assert len(pem_files) == 1
    assert stat.S_IMODE(pem_files[0].stat().st_mode) == 0o600
    meta_mode = stat.S_IMODE((keys_dir / "meta.json").stat().st_mode)
    assert meta_mode == 0o600
