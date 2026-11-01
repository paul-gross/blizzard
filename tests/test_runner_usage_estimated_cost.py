"""The runner's ``usage_facts`` row keeps the invocation's estimate beside its billed cost."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa

from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.store.schema import outbound_buffer, usage_facts
from tests.runner_fakes import FIXTURE_RUNNER_ID, make_store, record_usage

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)


def _sample(*, cost: float | None, estimate: float | None) -> UsageSample:
    return UsageSample(
        kind="spawn",
        model="gpt-5",
        input_tokens=10,
        output_tokens=20,
        cache_read_tokens=0,
        cache_create_tokens=0,
        cost_usd=cost,
        estimated_cost_usd=estimate,
    )


def _record(store, lease: str, sample: UsageSample) -> None:
    record_usage(
        store,
        lease_id=lease,
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=1,
        sample=sample,
        recorded_at=_NOW,
    )


def _rows(tmp_path: Path):
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        facts = conn.execute(
            sa.select(usage_facts.c.lease_id, usage_facts.c.estimated_cost_usd)
            .where(usage_facts.c.runner_id == FIXTURE_RUNNER_ID)
            .order_by(usage_facts.c.id)
        ).all()
        payloads = (
            conn.execute(
                sa.select(outbound_buffer.c.payload)
                .where(outbound_buffer.c.runner_id == FIXTURE_RUNNER_ID)
                .order_by(outbound_buffer.c.seq)
            )
            .scalars()
            .all()
        )
    return facts, [json.loads(p)["estimated_cost_usd"] for p in payloads]


def test_the_row_keeps_the_same_estimate_the_outbound_fact_carries(tmp_path: Path):
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    _record(store, "lease_est", _sample(cost=None, estimate=0.42))
    _record(store, "lease_none", _sample(cost=1.5, estimate=None))

    facts, outbound = _rows(tmp_path)
    assert [(r.lease_id, r.estimated_cost_usd) for r in facts] == [
        ("lease_est", pytest.approx(0.42)),
        ("lease_none", None),
    ]
    assert outbound == [pytest.approx(0.42), None]


def test_the_estimate_counts_toward_the_usage_totals(tmp_path: Path):
    with_estimate = make_store(f"sqlite:///{tmp_path / 'a.db'}")
    without_estimate = make_store(f"sqlite:///{tmp_path / 'b.db'}")
    for store, estimate in ((with_estimate, 0.42), (without_estimate, None)):
        _record(store, "lease_billed", _sample(cost=1.5, estimate=None))
        _record(store, "lease_unbilled", _sample(cost=None, estimate=estimate))

    estimated, unestimated = with_estimate.usage_since(_NOW), without_estimate.usage_since(_NOW)
    # The columns stay apart; only the figure sums them.
    assert estimated.cost_usd == unestimated.cost_usd == pytest.approx(1.5)
    assert estimated.estimated_cost_usd == pytest.approx(0.42)
    assert estimated.spend_usd == pytest.approx(1.92)
    assert estimated.cost_partial is False
    assert unestimated.estimated_cost_usd is None
    assert unestimated.cost_partial is True
