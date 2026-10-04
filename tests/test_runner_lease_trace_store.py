"""The lease trace sweep's store reads and writes (component tier) — the closed-lease window, the cursor
and the failure latch, over a real runner store."""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path

import pytest
import sqlalchemy as sa
from sqlalchemy.exc import IntegrityError

from blizzard.runner.tracing.cursor import LeaseCursorKey
from blizzard.runner.tracing.repository import LeaseTraceCheckpoint
from tests import runner_trace_fixtures as fx
from tests.runner_fakes import SqlAlchemyRunnerStore, make_store, runner_migration_prototype
from tests.runner_trace_leases import close, closed_lease

pytestmark = pytest.mark.component

_START = LeaseCursorKey.opening(fx.at(0))


def _store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}")


def test_closed_leases_read_in_first_closure_order_after_the_key(tmp_path: Path) -> None:
    store = _store(tmp_path)
    closed_lease(store, "l-b", opened=fx.at(1), closed=fx.at(10))
    closed_lease(store, "l-a", opened=fx.at(1), closed=fx.at(10))
    closed_lease(store, "l-c", opened=fx.at(1), closed=fx.at(5))
    close(store, "l-c", fx.at(20))  # a second closure never moves a lease

    keys = store.closed_leases_after(_START, fx.at(30), 10)

    assert keys == (
        LeaseCursorKey(fx.at(5), "l-c"),
        LeaseCursorKey(fx.at(10), "l-a"),
        LeaseCursorKey(fx.at(10), "l-b"),
    )
    assert store.closed_leases_after(keys[1], fx.at(30), 10) == (keys[2],)
    assert store.closed_leases_after(keys[2], fx.at(30), 10) == ()


def test_the_window_ends_at_the_settle_boundary_inclusive_and_truncates_at_the_limit(tmp_path: Path) -> None:
    store = _store(tmp_path)
    for i, lease_id in enumerate(("l-1", "l-2", "l-3")):
        closed_lease(store, lease_id, opened=fx.at(0), closed=fx.at(10 + i))

    assert [k.lease_id for k in store.closed_leases_after(_START, fx.at(11), 10)] == ["l-1", "l-2"]
    assert [k.lease_id for k in store.closed_leases_after(_START, fx.at(30), 2)] == ["l-1", "l-2"]
    assert store.oldest_unsent_lease(_START, fx.at(9)) is None
    assert store.oldest_unsent_lease(LeaseCursorKey(fx.at(10), "l-1"), fx.at(30)) == LeaseCursorKey(fx.at(11), "l-2")


def test_the_newest_cursor_row_is_the_position(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.newest_trace_cursor() is None
    first = LeaseTraceCheckpoint(_START, 0, fx.at(1))
    second = LeaseTraceCheckpoint(LeaseCursorKey(fx.at(5), "l-1"), 3, fx.at(2))

    store.append_trace_cursor(first)
    store.append_trace_cursor(second)

    assert store.newest_trace_cursor() == second


def test_a_latch_row_lands_with_its_hub_bound_report(tmp_path: Path) -> None:
    store = _store(tmp_path)
    assert store.newest_trace_latch() is None

    seq = store.record_trace_latch(
        "trace-export-failed", at=fx.at(1), report_kind="event.recorded", report_payload='{"kind": "x"}'
    )
    store.record_trace_latch(
        "trace-export-recovered", at=fx.at(1) + timedelta(seconds=1), report_kind="event.recorded", report_payload="{}"
    )

    assert store.newest_trace_latch() == "trace-export-recovered"
    pending = store.pending_outbound()
    assert len(pending) == 2
    report = pending[0]
    assert (report.seq, report.kind, report.chunk_id, report.lease_id) == (seq, "event.recorded", None, None)
    assert json.loads(report.payload) == {"kind": "x"}


def test_a_failed_report_insert_leaves_no_latch_row(tmp_path: Path) -> None:
    store = _store(tmp_path)

    with pytest.raises(IntegrityError):
        store.record_trace_latch(
            "trace-export-failed",
            at=fx.at(1),
            report_kind=None,  # type: ignore[arg-type]
            report_payload="{}",
        )

    assert store.newest_trace_latch() is None


def test_the_migrated_store_carries_the_cursor_tables_and_the_window_index() -> None:
    engine = sa.create_engine(f"sqlite:///{runner_migration_prototype()}")
    try:
        inspector = sa.inspect(engine)
        assert {"trace_cursor", "trace_export_latch"} <= set(inspector.get_table_names())
        indexes = {i["name"]: i["column_names"] for i in inspector.get_indexes("lease_closures")}
    finally:
        engine.dispose()
    assert indexes["ix_lease_closures_closed_at_lease_id"] == ["closed_at", "lease_id"]
