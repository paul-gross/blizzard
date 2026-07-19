"""Lease-token authorization (unit tier) — ``check_lease_token`` (issue #113, Phase 2).

A pure function of already-loaded values (``bzh:domain-takes-objects``): no store, no
HTTP, no clock — mirroring ``tests/test_route_auth.py``'s shape for ``check_route_token``.
"""

from __future__ import annotations

import hashlib

import pytest

from blizzard.runner.domain.lease_auth import check_lease_token

pytestmark = pytest.mark.unit


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def test_matching_token_passes() -> None:
    assert check_lease_token(presented_token="tok-good", stored_hash=_hash("tok-good")) is True


def test_mismatched_token_is_rejected() -> None:
    assert check_lease_token(presented_token="tok-wrong", stored_hash=_hash("tok-good")) is False


def test_missing_presented_token_is_rejected() -> None:
    assert check_lease_token(presented_token=None, stored_hash=_hash("tok-good")) is False


def test_no_stored_hash_is_rejected_even_with_a_presented_token() -> None:
    """A lease that never minted a token (or an unknown lease) authorizes nothing."""
    assert check_lease_token(presented_token="tok-good", stored_hash=None) is False


def test_both_absent_is_rejected() -> None:
    assert check_lease_token(presented_token=None, stored_hash=None) is False
