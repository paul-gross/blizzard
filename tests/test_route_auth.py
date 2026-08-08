"""Route-token authorization (unit tier) — ``RouteToken``, facts only.

A pure value over :class:`ChunkFacts` plus the live route's runner_id
(``bzh:domain-takes-objects``): no store, no HTTP, no clock — the same shape
``test_chunk_status_derivation.py`` holds its derivations to.
"""

from __future__ import annotations

import hashlib
from datetime import UTC, datetime

import pytest

from blizzard.hub.config import ROUTE_TOKEN_ENFORCE, ROUTE_TOKEN_WARN
from blizzard.hub.domain.route_auth import RouteToken
from blizzard.hub.domain.work import ChunkFacts, RouteCreatedFact, RouteReleasedFact, RouteTokenMintedFact

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _live_facts(*, token: str = "tok-good", route_seq: int = 0, token_seq: int = 0) -> ChunkFacts:
    return ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0, seq=route_seq)],
        route_tokens_minted=[RouteTokenMintedFact(token_hash=_hash(token), minted_at=_T0, seq=token_seq)],
    )


def test_matching_token_and_runner_passes_under_enforce() -> None:
    facts = _live_facts(token="tok-good")

    detail = RouteToken(
        facts=facts,
        presented="tok-good",
        submission_runner_id="r1",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert detail is None


def test_mismatched_token_is_rejected_under_enforce() -> None:
    facts = _live_facts(token="tok-good")

    detail = RouteToken(
        facts=facts,
        presented="tok-wrong",
        submission_runner_id="r1",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert detail is not None


def test_missing_token_is_rejected_under_enforce() -> None:
    facts = _live_facts(token="tok-good")

    detail = RouteToken(
        facts=facts,
        presented=None,
        submission_runner_id="r1",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert detail is not None


def test_mismatched_runner_is_rejected_under_enforce_even_with_the_right_token() -> None:
    facts = _live_facts(token="tok-good")

    detail = RouteToken(
        facts=facts,
        presented="tok-good",
        submission_runner_id="r2",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert detail is not None


def test_no_live_route_is_rejected_under_enforce() -> None:
    facts = ChunkFacts(minted=True)  # never claimed — no route, no token

    detail = RouteToken(
        facts=facts,
        presented="tok-good",
        submission_runner_id="r1",
        route_runner_id=None,
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert detail is not None


def test_released_route_has_no_live_token_and_is_rejected_under_enforce() -> None:
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0, seq=0)],
        route_tokens_minted=[RouteTokenMintedFact(token_hash=_hash("tok-good"), minted_at=_T0, seq=0)],
        routes_released=[RouteReleasedFact(released_at=_T0, seq=1)],
    )

    detail = RouteToken(
        facts=facts,
        presented="tok-good",
        submission_runner_id="r1",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert detail is not None


def test_a_rekeyed_token_supersedes_the_prior_one() -> None:
    """Newest-fact-wins: a later token fact (the re-key) is the one that authorizes;
    the prior plaintext no longer matches."""
    facts = ChunkFacts(
        minted=True,
        routes_created=[RouteCreatedFact(created_at=_T0, seq=0)],
        route_tokens_minted=[
            RouteTokenMintedFact(token_hash=_hash("tok-original"), minted_at=_T0, seq=0),
            RouteTokenMintedFact(token_hash=_hash("tok-rekeyed"), minted_at=_T0, seq=1),
        ],
    )

    stale = RouteToken(
        facts=facts,
        presented="tok-original",
        submission_runner_id="r1",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)
    fresh = RouteToken(
        facts=facts,
        presented="tok-rekeyed",
        submission_runner_id="r1",
        route_runner_id="r1",
    ).rejection(mode=ROUTE_TOKEN_ENFORCE)

    assert stale is not None
    assert fresh is None


def test_warn_mode_never_rejects_regardless_of_failure() -> None:
    facts = ChunkFacts(minted=True)  # no live route at all — the strongest failure

    detail = RouteToken(
        facts=facts,
        presented=None,
        submission_runner_id="r1",
        route_runner_id=None,
    ).rejection(mode=ROUTE_TOKEN_WARN)

    assert detail is None
