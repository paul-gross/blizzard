"""PKCE S256 challenge/verifier — ``blizzard.hub.auth.pkce`` (unit tier, issue #96).

Both the CLI (minting the challenge, ``hub/cli_login.py``) and the hub (verifying it
at ``POST /api/auth/cli/token``, ``hub/auth/service.py``) call this exact class — this
pins the encoding itself, independent of either caller.
"""

from __future__ import annotations

import base64
import hashlib

import pytest

from blizzard.hub.auth.pkce import Pkce

pytestmark = pytest.mark.unit


def test_challenge_is_rfc7636_s256() -> None:
    verifier = "a-fixed-verifier-value"
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode("ascii")).digest()).rstrip(b"=").decode("ascii")
    assert Pkce(verifier).challenge == expected


def test_challenge_carries_no_padding() -> None:
    assert "=" not in Pkce("any-verifier-value").challenge


def test_matches_accepts_the_matching_verifier() -> None:
    pkce = Pkce("correct-horse-battery-staple")
    assert pkce.matches(pkce.challenge) is True


def test_matches_rejects_a_wrong_verifier() -> None:
    assert Pkce("a-different-verifier").matches(Pkce("the-real-verifier").challenge) is False


def test_matches_rejects_a_garbage_challenge() -> None:
    assert Pkce("some-verifier").matches("not-a-real-challenge") is False


def test_new_mints_a_verifier_in_the_rfc7636_length_range() -> None:
    assert 43 <= len(Pkce.new().verifier) <= 128
