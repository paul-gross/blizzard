"""Host wiring preserves the connection binding on cross-kind writes."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path
from typing import TypedDict

import pytest
from sqlalchemy import func, select

from blizzard.foundation.clock import SystemClock
from blizzard.foundation.logging import get_logger
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RunnerStoreErrorFactory
from blizzard.runner.store.scoped import RunnerUnbound
from blizzard.runner.store.wiring import build_host_stores
from tests.support.runner_fakes import make_store


class _Attempt(TypedDict):
    slug: str
    sampled_at: datetime
    payload: str
    report_kind: str
    report_payload: str


def test_cross_kind_report_is_bound_or_rolled_back(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'host.db'}")
    errors = RunnerStoreErrorFactory(get_logger("test"))
    attempt: _Attempt = {
        "slug": "sample",
        "sampled_at": datetime(2026, 1, 1, tzinfo=UTC),
        "payload": "{}",
        "report_kind": "sampled",
        "report_payload": "{}",
    }
    unbound = build_host_stores(store.engine, errors=errors, clock=SystemClock())
    with pytest.raises(RunnerUnbound):
        unbound.external_usage.record_external_usage_attempt(**attempt)
    with store.engine.connect() as conn:
        assert conn.execute(select(func.count()).select_from(schema.external_usage_samples)).scalar_one() == 0
    bound = build_host_stores(store.engine, errors=errors, clock=SystemClock(), runner_id="rn_test")
    bound.external_usage.record_external_usage_attempt(**attempt)
    with store.engine.connect() as conn:
        assert (
            conn.execute(
                select(schema.outbound_buffer.c.runner_id).where(schema.outbound_buffer.c.runner_id == "rn_test")
            ).scalar_one()
            == "rn_test"
        )
        assert conn.execute(select(func.count()).select_from(schema.external_usage_samples)).scalar_one() == 1
