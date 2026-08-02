"""The mid-run marker-write capability authority (issue #230, phase 1) — unit tier.

A plain in-memory issue/verify/revoke seam, keyed by ``(chunk_id, node_id, epoch)`` —
no store, no HTTP, no clock.
"""

from __future__ import annotations

import itertools

import pytest

from blizzard.hub.delivery.marker_auth import MarkerAuthority

pytestmark = pytest.mark.unit


def _sequential_tokens():  # type: ignore[no-untyped-def]
    counter = itertools.count()
    return lambda: f"tok-{next(counter)}"


def test_issue_then_verify_round_trips() -> None:
    authority = MarkerAuthority()

    token = authority.issue("ch_1", node_id="nd_1", epoch=1)

    assert authority.verify(token, chunk_id="ch_1", node_id="nd_1", epoch=1) is True


def test_verify_fails_against_a_different_node_id() -> None:
    authority = MarkerAuthority()
    token = authority.issue("ch_1", node_id="nd_1", epoch=1)

    assert authority.verify(token, chunk_id="ch_1", node_id="nd_2", epoch=1) is False


def test_verify_fails_against_a_different_epoch() -> None:
    authority = MarkerAuthority()
    token = authority.issue("ch_1", node_id="nd_1", epoch=1)

    assert authority.verify(token, chunk_id="ch_1", node_id="nd_1", epoch=2) is False


def test_verify_fails_against_a_different_chunk_id() -> None:
    authority = MarkerAuthority()
    token = authority.issue("ch_1", node_id="nd_1", epoch=1)

    assert authority.verify(token, chunk_id="ch_2", node_id="nd_1", epoch=1) is False


def test_a_revoked_token_fails_to_verify() -> None:
    authority = MarkerAuthority()
    token = authority.issue("ch_1", node_id="nd_1", epoch=1)

    authority.revoke("ch_1", node_id="nd_1", epoch=1)

    assert authority.verify(token, chunk_id="ch_1", node_id="nd_1", epoch=1) is False


def test_revoke_is_safe_when_nothing_was_ever_issued() -> None:
    authority = MarkerAuthority()

    authority.revoke("ch_1", node_id="nd_1", epoch=1)  # must not raise


def test_verifying_an_unknown_key_returns_false_not_raise() -> None:
    authority = MarkerAuthority()

    assert authority.verify("some-token", chunk_id="ch_never", node_id="nd_never", epoch=1) is False


def test_two_issues_for_different_keys_never_verify_against_each_others_key() -> None:
    """Distinct keys — differing chunk, node, or epoch — mint tokens that don't cross-verify."""
    authority = MarkerAuthority(token_factory=_sequential_tokens())

    token_epoch_1 = authority.issue("ch_a", node_id="nd_x", epoch=1)
    token_epoch_2 = authority.issue("ch_a", node_id="nd_x", epoch=2)
    token_other_node = authority.issue("ch_a", node_id="nd_y", epoch=1)
    token_other_chunk = authority.issue("ch_b", node_id="nd_x", epoch=1)

    assert authority.verify(token_epoch_1, chunk_id="ch_a", node_id="nd_x", epoch=1) is True
    assert authority.verify(token_epoch_2, chunk_id="ch_a", node_id="nd_x", epoch=2) is True
    assert authority.verify(token_other_node, chunk_id="ch_a", node_id="nd_y", epoch=1) is True
    assert authority.verify(token_other_chunk, chunk_id="ch_b", node_id="nd_x", epoch=1) is True

    # Cross-checks: none of these tokens verify against a key other than its own.
    assert authority.verify(token_epoch_1, chunk_id="ch_a", node_id="nd_x", epoch=2) is False
    assert authority.verify(token_epoch_2, chunk_id="ch_a", node_id="nd_x", epoch=1) is False
    assert authority.verify(token_other_node, chunk_id="ch_a", node_id="nd_x", epoch=1) is False
    assert authority.verify(token_other_chunk, chunk_id="ch_a", node_id="nd_x", epoch=1) is False


def test_re_issuing_for_the_same_key_replaces_the_prior_token() -> None:
    authority = MarkerAuthority()
    stale = authority.issue("ch_1", node_id="nd_1", epoch=1)
    fresh = authority.issue("ch_1", node_id="nd_1", epoch=1)

    assert authority.verify(stale, chunk_id="ch_1", node_id="nd_1", epoch=1) is False
    assert authority.verify(fresh, chunk_id="ch_1", node_id="nd_1", epoch=1) is True


def test_default_token_factory_mints_distinct_url_safe_tokens() -> None:
    authority = MarkerAuthority()

    a = authority.issue("ch_1", node_id="nd_1", epoch=1)
    b = authority.issue("ch_1", node_id="nd_2", epoch=1)

    assert a != b
    assert a and b
