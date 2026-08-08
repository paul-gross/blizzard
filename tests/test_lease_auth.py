"""Lease-token authorization (unit tier) — ``LeaseToken`` (issue #113, Phase 2).

A pure value over already-loaded values (``bzh:domain-takes-objects``): no store, no
HTTP, no clock — mirroring ``tests/test_route_auth.py``'s shape for ``RouteToken``.
"""

from __future__ import annotations

import hashlib

import pytest

from blizzard.runner.domain.lease_auth import LeaseToken

pytestmark = pytest.mark.unit


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_matching_token_passes() -> None:
    assert LeaseToken("tok-good", _hash("tok-good")).valid is True


def test_mismatched_token_is_rejected() -> None:
    assert LeaseToken("tok-wrong", _hash("tok-good")).valid is False


def test_missing_presented_token_is_rejected() -> None:
    assert LeaseToken(None, _hash("tok-good")).valid is False


def test_no_stored_hash_is_rejected_even_with_a_presented_token() -> None:
    """A lease that never minted a token (or an unknown lease) authorizes nothing."""
    assert LeaseToken("tok-good", None).valid is False


def test_both_absent_is_rejected() -> None:
    assert LeaseToken(None, None).valid is False
