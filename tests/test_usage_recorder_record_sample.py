"""The real ``UsageRecorder.record_sample``: the lease's own harness identity stamped onto the
sample, the session's banked basis, the domain cost policy, then the store's write — the
persisted cost, the rejected-reading warning, and the one fact-changed frame per write."""

from __future__ import annotations

import json
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from structlog.testing import capture_logs

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import USAGE_RECORDED
from blizzard.runner.events.broker import EventBroker
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID, SessionReference
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.leases import Lease, NewLease
from blizzard.runner.store.schema import usage_facts
from blizzard.runner.usage.recorder import UsageRecorder
from tests.runner_fakes import make_store, make_usage_recorder

pytestmark = pytest.mark.unit

_NOW = datetime(2026, 10, 5, 12, 0, 0, tzinfo=UTC)
_REJECTED = "harness cost figure reads below what its session already banked"


def _sample(kind: UsageKind, *, tokens: int, cost: float | None, scope: int | None) -> UsageSample:
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


def _setup(tmp_path: Path) -> tuple[UsageRecorder, EventBroker, Lease]:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=3,
            retries_max=2,
            created_at=_NOW,
        )
    )
    store.record_spawn(
        "lease_1",
        pid=1,
        process_start_time="1",
        session=SessionReference(CLAUDE_CODE_HARNESS_ID, "sess-1"),
        harness_version="2.1.0",
        spawned_at=_NOW,
    )
    events = EventBroker()
    recorder = replace(make_usage_recorder(store, FixedClock(_NOW)), events=events)
    lease = store.active_lease("lease_1")
    assert lease is not None
    return recorder, events, lease


def _rows(tmp_path: Path) -> list[sa.Row]:
    engine = sa.create_engine(f"sqlite:///{tmp_path / 'runner.db'}")
    with engine.connect() as conn:
        return list(
            conn.execute(
                sa.select(
                    usage_facts.c.kind,
                    usage_facts.c.epoch,
                    usage_facts.c.generation,
                    usage_facts.c.harness_id,
                    usage_facts.c.harness_version,
                    usage_facts.c.cost_usd,
                    usage_facts.c.reported_cost_usd,
                    usage_facts.c.recorded_at,
                ).order_by(usage_facts.c.id)
            )
        )


def _fact_frames(events: EventBroker) -> list[dict[str, object]]:
    return [json.loads(e.data) for e in events.snapshot() if e.type == "fact-changed"]


def test_a_sample_is_stamped_with_the_leases_harness_and_banks_its_own_share(tmp_path: Path) -> None:
    recorder, events, lease = _setup(tmp_path)

    with capture_logs() as logs:
        recorder.record_sample(lease, generation=1, sample=_sample("spawn", tokens=100_000, cost=2.0, scope=100_000))
        # The judge resumes the same session: its figure carries the worker's 2.00, so it banks 0.50.
        recorder.record_sample(lease, generation=1, sample=_sample("judge", tokens=5_000, cost=2.5, scope=105_000))

    rows = _rows(tmp_path)
    assert [(r.kind, r.epoch, r.generation, r.harness_id, r.harness_version) for r in rows] == [
        ("spawn", 3, 1, CLAUDE_CODE_HARNESS_ID, "2.1.0"),
        ("judge", 3, 1, CLAUDE_CODE_HARNESS_ID, "2.1.0"),
    ]
    assert [r.cost_usd for r in rows] == [pytest.approx(2.0), pytest.approx(0.5)]
    assert [r.reported_cost_usd for r in rows] == [pytest.approx(2.0), pytest.approx(2.5)]
    assert all(r.recorded_at.replace(tzinfo=UTC) == _NOW for r in rows)
    assert [e for e in logs if e["event"] == _REJECTED] == []
    assert [(f["kind"], f["chunk_id"], f["lease_id"]) for f in _fact_frames(events)] == [
        (USAGE_RECORDED, "ch_1", "lease_1"),
        (USAGE_RECORDED, "ch_1", "lease_1"),
    ]


def test_a_figure_below_the_banked_basis_is_persisted_costless_and_warned_once(tmp_path: Path) -> None:
    recorder, events, lease = _setup(tmp_path)
    recorder.record_sample(lease, generation=1, sample=_sample("spawn", tokens=100_000, cost=2.0, scope=100_000))
    below = _sample("resume", tokens=5_000, cost=1.5, scope=105_000)

    with capture_logs() as logs:
        recorder.record_sample(lease, generation=2, sample=below)
    with capture_logs() as replay_logs:
        # The exact replay writes nothing, so it neither warns nor announces a second time.
        recorder.record_sample(lease, generation=2, sample=below)
    assert [e for e in replay_logs if e["event"] == _REJECTED] == []

    rows = _rows(tmp_path)
    assert [(r.kind, r.cost_usd, r.reported_cost_usd) for r in rows] == [
        ("spawn", pytest.approx(2.0), pytest.approx(2.0)),
        ("resume", None, pytest.approx(1.5)),
    ]
    warnings = [e for e in logs if e["event"] == _REJECTED]
    assert len(warnings) == 1
    assert warnings[0]["log_level"] == "warning"
    assert (warnings[0]["lease_id"], warnings[0]["chunk_id"], warnings[0]["generation"]) == ("lease_1", "ch_1", 2)
    assert warnings[0]["reported_cost_usd"] == pytest.approx(1.5)
    assert len(_fact_frames(events)) == 2


def test_a_sessionless_lease_records_the_samples_own_identity_unstamped(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    store.record_lease(
        NewLease(
            lease_id="lease_1",
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=1,
            retries_max=2,
            created_at=_NOW,
        )
    )
    lease = store.active_lease("lease_1")
    assert lease is not None and lease.session is None

    make_usage_recorder(store, FixedClock(_NOW)).record_sample(
        lease, generation=1, sample=replace(_sample("spawn", tokens=10, cost=0.25, scope=10), harness_id="own")
    )

    assert [(r.harness_id, r.harness_version, r.cost_usd) for r in _rows(tmp_path)] == [
        ("own", None, pytest.approx(0.25))
    ]
