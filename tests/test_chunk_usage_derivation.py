"""Chunk usage derivation (unit tier) — the derived cost/token total, facts only.

Usage is a fact, never a stored aggregate (``bzh:facts-not-status``): a chunk's total is
a **sum over facts at read time**, the same discipline :func:`derive_chunk_status`
established. These tests build :class:`ChunkFacts` directly — no store, no tokens
(``bzh:domain-takes-objects``) — and pin the cost-absent lower-bound + PARTIAL
treatment: a row with no ``cost_usd`` still contributes its tokens, but flags the
chunk's derived cost as a lower bound rather than the true spend.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest

from blizzard.hub.domain.work import ChunkFacts, UsageFact, derive_chunk_usage

pytestmark = pytest.mark.unit

_T0 = datetime(2026, 1, 1, tzinfo=UTC)


def _at(seconds: int) -> datetime:
    return _T0 + timedelta(seconds=seconds)


def _usage(
    *,
    node_id: str = "nd_1",
    epoch: int = 1,
    kind: str = "spawn",
    model: str = "claude-opus-4-8",
    input_tokens: int = 0,
    output_tokens: int = 0,
    cache_read_tokens: int = 0,
    cache_create_tokens: int = 0,
    cost_usd: float | None = 0.0,
    recorded_at: datetime | None = None,
) -> UsageFact:
    return UsageFact(
        node_id=node_id,
        epoch=epoch,
        kind=kind,
        model=model,
        input_tokens=input_tokens,
        output_tokens=output_tokens,
        cache_read_tokens=cache_read_tokens,
        cache_create_tokens=cache_create_tokens,
        cost_usd=cost_usd,
        recorded_at=recorded_at or _at(0),
    )


def test_no_usage_facts_derives_a_zero_non_partial_total() -> None:
    usage = derive_chunk_usage(ChunkFacts(minted=True))
    assert usage.input_tokens == 0
    assert usage.output_tokens == 0
    assert usage.cache_read_tokens == 0
    assert usage.cache_create_tokens == 0
    assert usage.cost_usd == 0.0
    assert usage.cost_partial is False


def test_derive_chunk_usage_sums_every_row_by_token_class_and_cost() -> None:
    facts = ChunkFacts(
        minted=True,
        usage=[
            _usage(
                input_tokens=100,
                output_tokens=50,
                cache_read_tokens=10,
                cache_create_tokens=5,
                cost_usd=0.10,
                recorded_at=_at(1),
            ),
            _usage(
                node_id="nd_2",
                kind="judge",
                input_tokens=200,
                output_tokens=75,
                cache_read_tokens=20,
                cache_create_tokens=0,
                cost_usd=0.25,
                recorded_at=_at(2),
            ),
        ],
    )
    usage = derive_chunk_usage(facts)
    assert usage.input_tokens == 300
    assert usage.output_tokens == 125
    assert usage.cache_read_tokens == 30
    assert usage.cache_create_tokens == 5
    assert usage.cost_usd == pytest.approx(0.35)
    assert usage.cost_partial is False


def test_a_cost_absent_row_contributes_tokens_but_flags_the_total_partial() -> None:
    facts = ChunkFacts(
        minted=True,
        usage=[
            _usage(input_tokens=100, output_tokens=50, cost_usd=0.10, recorded_at=_at(1)),
            # Envelope-less transcript-summation fallback: tokens known, cost unknown.
            _usage(input_tokens=40, output_tokens=10, cost_usd=None, recorded_at=_at(2)),
        ],
    )
    usage = derive_chunk_usage(facts)
    assert usage.input_tokens == 140
    assert usage.output_tokens == 60
    # cost_usd is a lower bound: only the rows that carried a cost are summed.
    assert usage.cost_usd == pytest.approx(0.10)
    assert usage.cost_partial is True


def test_every_row_carrying_cost_derives_a_non_partial_total() -> None:
    facts = ChunkFacts(
        minted=True,
        usage=[
            _usage(cost_usd=0.01, recorded_at=_at(1)),
            _usage(cost_usd=0.02, recorded_at=_at(2)),
        ],
    )
    assert derive_chunk_usage(facts).cost_partial is False


def test_stale_epoch_usage_row_is_still_summed() -> None:
    # Usage is NOT epoch-fenced: a row minted at an epoch behind the chunk's latest
    # (e.g. a fenced-out zombie's real spend) is summed exactly like any other row.
    facts = ChunkFacts(
        minted=True,
        usage=[
            _usage(epoch=1, cost_usd=0.05, recorded_at=_at(1)),
            _usage(epoch=2, cost_usd=0.05, recorded_at=_at(2)),
            # A stale-epoch row (epoch=1) recorded after the epoch=2 lease — still counted.
            _usage(epoch=1, cost_usd=0.05, recorded_at=_at(3)),
        ],
    )
    usage = derive_chunk_usage(facts)
    assert usage.cost_usd == pytest.approx(0.15)
    assert usage.cost_partial is False
