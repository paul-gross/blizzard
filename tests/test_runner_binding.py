"""A runner store's binding (component tier): rows written while it named no runner are stamped when it binds — at
registration's first adoption, in the identity write's commit, and by the start-up stamp — and the writers that can
run before registration hold off until it binds: ``PATCH /api/runner`` and ``POST /api/selftests`` answer 503 naming
the cause, and a rejected tracing setting's report waits."""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path

import pytest
import sqlalchemy as sa
from fastapi.testclient import TestClient

from blizzard.foundation.clock import FixedClock
from blizzard.foundation.fact_kinds import EVENT_RECORDED
from blizzard.foundation.trace_export.settings import TracingSettings
from blizzard.runner.app import create_app
from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.adapter import WorkerHandle
from blizzard.runner.harness.identity import CLAUDE_CODE_HARNESS_ID
from blizzard.runner.harness.registry import HarnessBinding, HarnessRegistry
from blizzard.runner.hub.identity import RunnerIdentity
from blizzard.runner.store import schema
from blizzard.runner.store.errors import RUNNER_SCOPED_OPTION
from blizzard.runner.store.internal.unowned_row_stamp import UnownedRowStamp
from blizzard.runner.tracing.sweep import PendingTracingRejection
from tests.runner_fakes import FakeHarness, SqlAlchemyRunnerStore, make_store

pytestmark = pytest.mark.component

_NOW = datetime(2026, 10, 11, 12, 0, tzinfo=UTC)
_REJECTED = TracingSettings.of({"OTEL_EXPORTER_OTLP_ENDPOINT": "http://x:4318", "OTEL_EXPORTER_OTLP_PROTOCOL": "grpc"})


@dataclass
class _Binding:
    runner_id: str | None


def _unbound_store(tmp_path: Path) -> SqlAlchemyRunnerStore:
    return make_store(f"sqlite:///{tmp_path / 'runner.db'}", runner_id=None)


def _write_rows(store: SqlAlchemyRunnerStore, runner_id: str | None, *chunk_ids: str) -> None:
    """Pause-park rows as the previous release (``runner_id`` None) or another runner wrote them."""
    with store._engine.begin() as conn:
        conn.execution_options(**{RUNNER_SCOPED_OPTION: True}).execute(
            sa.insert(schema.pause_parks),
            [{"lease_id": f"lease_{c}", "chunk_id": c, "parked_at": _NOW, "runner_id": runner_id} for c in chunk_ids],
        )


def _owners(store: SqlAlchemyRunnerStore) -> dict[str, str | None]:
    parks = schema.pause_parks
    with store._engine.connect() as conn:
        rows = conn.execution_options(**{RUNNER_SCOPED_OPTION: True}).execute(
            sa.select(parks.c.chunk_id, parks.c.runner_id)
        )
        return {str(r.chunk_id): r.runner_id for r in rows}


def test_the_first_adoption_binds_and_stamps_the_unowned_rows_in_its_commit(tmp_path: Path) -> None:
    store = _unbound_store(tmp_path)
    _write_rows(store, None, "ch_old")
    assert store.pause_parked_lease_ids() == set()  # unbound: reads answer as an empty store

    store.record_runner_identity(RunnerIdentity("rn_a", "runner-local", _NOW))

    assert store.connections.runner_id == "rn_a"
    assert _owners(store) == {"ch_old": "rn_a"}
    assert store.pause_parked_lease_ids() == {"lease_ch_old"}


def test_the_start_up_stamp_claims_only_unowned_rows_and_is_idempotent(tmp_path: Path) -> None:
    store = _unbound_store(tmp_path)
    _write_rows(store, None, "ch_1", "ch_2")
    _write_rows(store, "rn_other", "ch_3")
    stamp = UnownedRowStamp(store.connections)

    assert stamp.stamp("rn_a") == 2
    assert stamp.stamp("rn_a") == 0
    assert _owners(store) == {"ch_1": "rn_a", "ch_2": "rn_a", "ch_3": "rn_other"}


def test_patch_runner_answers_503_naming_the_cause_until_bound(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    with TestClient(create_app(config, binding=_Binding(None))) as client:
        resp = client.patch("/api/runner", json={"paused": True})

    assert resp.status_code == 503
    assert "not registered" in resp.json()["detail"]


def test_a_selftest_answers_503_until_bound_after_naming_an_unknown_harness(tmp_path: Path) -> None:
    config = RunnerConfig(root=tmp_path, db_url="sqlite://")
    adapter = FakeHarness(handle=WorkerHandle(session_id="s", pid=1, process_start_time="t", pgid=1), verdict=None)
    harnesses = HarnessRegistry({CLAUDE_CODE_HARNESS_ID: HarnessBinding(adapter=adapter)})
    with TestClient(create_app(config, harnesses=harnesses, binding=_Binding(None))) as client:
        refused = client.post("/api/selftests", json={"harness": CLAUDE_CODE_HARNESS_ID})
        unknown = client.post("/api/selftests", json={"harness": "codex"})

    assert refused.status_code == 503
    assert "not registered" in refused.json()["detail"]
    assert unknown.status_code == 422  # a misnamed harness is named whether or not the runner has registered


def test_a_rejected_tracing_report_waits_until_the_store_is_bound(tmp_path: Path) -> None:
    store = _unbound_store(tmp_path)
    rejection = PendingTracingRejection(
        _REJECTED, store, lambda: store.connections.runner_id is not None, FixedClock(_NOW)
    )

    rejection.announce()
    assert rejection.owed

    store.record_runner_identity(RunnerIdentity("rn_a", "runner-local", _NOW))
    rejection.announce()
    rejection.announce()

    assert not rejection.owed
    assert [f.kind for f in store.pending_outbound()] == [EVENT_RECORDED]
