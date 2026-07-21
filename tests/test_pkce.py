"""PKCE S256 challenge/verifier — ``blizzard.hub.auth.pkce`` (unit tier, issue #96).

Both the CLI (minting the challenge, ``hub/cli_login.py``) and the hub (verifying it
at ``POST /api/auth/cli/token``, ``hub/auth/service.py``) call these exact two
functions — this pins the encoding itself, independent of either caller.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from blizzard.hub.auth.pkce import challenge_from_verifier, verify_code_challenge

pytestmark = pytest.mark.unit


def test_challenge_from_verifier_is_rfc7636_s256() -> None:
    verifier = "a-fixed-verifier-value"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert challenge_from_verifier(verifier) == expected


def test_challenge_from_verifier_carries_no_padding() -> None:
    assert "=" not in challenge_from_verifier("any-verifier-value")


def test_verify_code_challenge_accepts_the_matching_verifier() -> None:
    verifier = "correct-horse-battery-staple"
    assert verify_code_challenge(verifier, challenge_from_verifier(verifier)) is True


def test_verify_code_challenge_rejects_a_wrong_verifier() -> None:
    challenge = challenge_from_verifier("the-real-verifier")
    assert verify_code_challenge("a-different-verifier", challenge) is False


def test_verify_code_challenge_rejects_a_garbage_challenge() -> None:
    assert verify_code_challenge("some-verifier", "not-a-real-challenge") is False
