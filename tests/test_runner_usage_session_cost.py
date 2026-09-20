"""Banking a session-scoped cost figure as per-invocation facts.

A harness whose envelope charges its figure against the whole session reports a total
that already contains every earlier invocation of that session — across the leases it
was resumed into, not just the generations of one. Each fact banks only its own share,
so a sum over facts is the session's real spend rather than a multiple of it."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
import sqlalchemy as sa

from blizzard.runner.domain.leases import NewLease
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.store.schema import usage_facts
from tests.runner_fakes import make_store

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 7, 13, 12, 0, 0, tzinfo=UTC)
_SESSION = "sess-shared"


def _store(tmp_path):  # type: ignore[no-untyped-def]
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def _spawned_lease(store, lease: str, *, chunk: str = "ch_1") -> None:  # type: ignore[no-untyped-def]
    """A lease already spawned onto the one shared session."""
    store.record_lease(
        NewLease(
            lease_id=lease,
            chunk_id=chunk,
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        lease,
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, _SESSION),
        spawned_at=_NOW,
    )


def _sample(kind: UsageKind, *, tokens: int, cost: float, scope: int | None) -> UsageSample:
    return UsageSample(
        kind=kind,
        model="claude-sonnet-5",
        input_tokens=0,
        output_tokens=0,
        cache_read_tokens=tokens,
        cache_create_tokens=0,
        cost_usd=cost,
        cost_scope_tokens=scope,
    )


def _record(store, lease: str, *, generation: int, sample: UsageSample) -> None:  # type: ignore[no-untyped-def]
    store.record_usage(
        lease_id=lease,
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=generation,
        sample=sample,
        recorded_at=_NOW,
    )


def test_a_judge_resuming_the_worker_session_banks_only_its_own_turn(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _spawned_lease(store, "lease_1")
    _record(store, "lease_1", generation=1, sample=_sample("spawn", tokens=100_000, cost=2.00, scope=100_000))
    # The judgement resumes that same session, so its figure carries the worker's spend.
    _record(store, "lease_1", generation=1, sample=_sample("judge", tokens=5_000, cost=2.10, scope=105_000))

    totals = store.usage_since(_NOW)
    assert totals.cost_usd == pytest.approx(2.10)  # the session's real spend, not 4.10


def test_a_session_resumed_into_a_later_lease_does_not_rebank_the_earlier_one(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _spawned_lease(store, "lease_1")
    _record(store, "lease_1", generation=1, sample=_sample("spawn", tokens=100_000, cost=2.00, scope=100_000))
    # A second lease resumes the same session — its figure still counts from the session's
    # own start, which is what a per-lease reading would bank a second time.
    _spawned_lease(store, "lease_2")
    _record(store, "lease_2", generation=1, sample=_sample("spawn", tokens=40_000, cost=2.90, scope=140_000))

    totals = store.usage_since(_NOW)
    assert totals.cost_usd == pytest.approx(2.90)
    assert totals.cache_read_tokens == 140_000  # tokens were always per-invocation


def test_the_harness_figure_is_kept_beside_the_cost_derived_from_it(tmp_path):  # type: ignore[no-untyped-def]
    store = _store(tmp_path)
    _spawned_lease(store, "lease_1")
    _record(store, "lease_1", generation=1, sample=_sample("spawn", tokens=100_000, cost=2.00, scope=100_000))
    _record(store, "lease_1", generation=1, sample=_sample("judge", tokens=5_000, cost=2.10, scope=105_000))

    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(usage_facts.c.kind, usage_facts.c.cost_usd, usage_facts.c.reported_cost_usd).order_by(
                usage_facts.c.id
            )
        ).all()
    assert [(r.kind, r.cost_usd, r.reported_cost_usd) for r in rows] == [
        ("spawn", pytest.approx(2.00), pytest.approx(2.00)),
        ("judge", pytest.approx(0.10), pytest.approx(2.10)),
    ]


def test_a_lease_with_no_identified_session_banks_its_figure_verbatim(tmp_path):  # type: ignore[no-untyped-def]
    """No session, no basis to read a figure against — and no earlier fact it could
    double-count, since a fact is only reachable through the lease that produced it."""
    store = _store(tmp_path)
    store.record_lease(
        NewLease(
            lease_id="lease_x",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            runner_id="r1",
            retries_max=2,
            created_at=_NOW,
        )
    )
    _record(store, "lease_x", generation=1, sample=_sample("spawn", tokens=100_000, cost=2.00, scope=100_000))
    assert store.usage_since(_NOW).cost_usd == pytest.approx(2.00)


def test_a_session_that_changes_scope_mid_flight_does_not_rebank_its_verbatim_facts(tmp_path):  # type: ignore[no-untyped-def]
    """The harness updates itself underneath the runner, mid-session: earlier envelopes
    carried no scope and rode verbatim, so what the session banked is a sum no single
    figure equals."""
    store = _store(tmp_path)
    _spawned_lease(store, "lease_1")
    for generation, cost in ((1, 1.00), (2, 2.00), (3, 0.50)):
        _record(store, "lease_1", generation=generation, sample=_sample("spawn", tokens=10_000, cost=cost, scope=None))
    # $3.50 banked over 30_000 tokens, of which the largest single figure is $2.00.
    _record(store, "lease_1", generation=4, sample=_sample("spawn", tokens=10_000, cost=4.20, scope=40_000))

    # $4.20 is what the harness says the session has now cost, so that is the total.
    assert store.usage_since(_NOW).cost_usd == pytest.approx(4.20)


def test_a_session_spanning_the_reading_change_charges_only_what_followed_it(tmp_path):  # type: ignore[no-untyped-def]
    """Facts banked before this reading hold the harness's running total rather than a
    share, so summing them as shares puts the basis above anything it reports next."""
    store = _store(tmp_path)
    _spawned_lease(store, "lease_1")
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    # $2.10 of real spend, recorded by the older reading as two running totals.
    with engine.begin() as conn:
        for generation, cumulative in ((1, 2.00), (2, 2.10)):
            conn.execute(
                usage_facts.insert().values(
                    lease_id="lease_1",
                    chunk_id="ch_1",
                    node_id="nd_build",
                    epoch=1,
                    generation=generation,
                    kind="spawn",
                    model="claude-sonnet-5",
                    input_tokens=0,
                    output_tokens=0,
                    cache_read_tokens=10_000,
                    cache_create_tokens=0,
                    cost_usd=cumulative,
                    reported_cost_usd=cumulative,
                    cost_is_share=False,
                    recorded_at=_NOW,
                )
            )
    _record(store, "lease_1", generation=3, sample=_sample("spawn", tokens=5_000, cost=2.50, scope=25_000))
    _record(store, "lease_1", generation=4, sample=_sample("spawn", tokens=5_000, cost=3.00, scope=30_000))

    with engine.connect() as conn:
        rows = conn.execute(
            sa.select(usage_facts.c.generation, usage_facts.c.cost_usd)
            .where(usage_facts.c.generation > 2)
            .order_by(usage_facts.c.id)
        ).all()
    # Read against the summed totals instead, both would have gone backwards and banked nothing.
    assert [r.cost_usd for r in rows] == [pytest.approx(0.40), pytest.approx(0.50)]
