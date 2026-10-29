"""The lease usage route — ``GET /api/leases/{lease_id}/usage``.

Exercised over a real store via ``TestClient``: one fact per ``(generation, kind)`` as the store
recorded it, a closed lease still readable, and an unknown lease a 404."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.usage import UsageSample
from blizzard.runner.leases.model import NewLease
from tests.config_inputs import create_runner_app
from tests.runner_client import runner_client
from tests.runner_fakes import make_host_stores, make_store, make_stores, record_usage
from tests.support import assert_all_timestamps_utc

pytestmark = pytest.mark.component

_NOW = datetime(2026, 7, 16, 12, 0, 0, tzinfo=UTC)


def _app(tmp_path: Path):  # type: ignore[no-untyped-def]
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    config = RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}")
    app = create_runner_app(config, runner_stores=make_stores(store), host_stores=make_host_stores(store))
    return app, store


def _seed_lease(store, lease_id: str, epoch: int) -> None:  # type: ignore[no-untyped-def]
    store.record_lease(
        NewLease(
            lease_id=lease_id,
            chunk_id="ch_1",
            graph_id="gr_1",
            node_id="nd_build",
            node_name="build",
            epoch=epoch,
            retries_max=2,
            created_at=_NOW,
        )
    )


def _record(store, lease_id: str, *, generation: int, kind: str, input_tokens: int, minutes: int) -> None:  # type: ignore[no-untyped-def]
    record_usage(
        store,
        lease_id=lease_id,
        chunk_id="ch_1",
        node_id="nd_build",
        epoch=1,
        generation=generation,
        sample=UsageSample(
            kind=kind,  # type: ignore[arg-type]
            model="mock-model",
            input_tokens=input_tokens,
            output_tokens=3,
            cache_read_tokens=1,
            cache_create_tokens=2,
            cost_usd=0.5,
            estimated_cost_usd=0.25,
            harness_id="opencode",
        ),
        recorded_at=_NOW + timedelta(minutes=minutes),
    )


def test_lists_one_fact_per_generation_and_kind_oldest_first(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    _seed_lease(store, "lease_1", 1)
    _seed_lease(store, "lease_2", 2)
    _record(store, "lease_1", generation=1, kind="spawn", input_tokens=10, minutes=0)
    _record(store, "lease_1", generation=1, kind="judge", input_tokens=20, minutes=1)
    _record(store, "lease_2", generation=1, kind="spawn", input_tokens=99, minutes=2)

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/usage")

    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["lease_id"] == "lease_1"
    assert [(f["generation"], f["kind"], f["input_tokens"]) for f in body["items"]] == [
        (1, "spawn", 10),
        (1, "judge", 20),
    ]
    first = body["items"][0]
    assert first["node_id"] == "nd_build"
    assert first["epoch"] == 1
    assert first["model"] == "mock-model"
    assert first["harness_id"] == "opencode"
    assert (first["output_tokens"], first["cache_read_tokens"], first["cache_create_tokens"]) == (3, 1, 2)
    assert first["cost_usd"] is not None
    assert first["estimated_cost_usd"] is not None
    assert_all_timestamps_utc(body)


def test_a_lease_with_no_facts_reads_empty(tmp_path: Path) -> None:
    app, store = _app(tmp_path)
    _seed_lease(store, "lease_1", 1)

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_1/usage")

    assert resp.status_code == 200, resp.text
    assert resp.json() == {"lease_id": "lease_1", "items": []}


def test_unknown_lease_is_404(tmp_path: Path) -> None:
    app, _store = _app(tmp_path)

    with runner_client(app) as client:
        resp = client.get("/api/leases/lease_missing/usage")

    assert resp.status_code == 404
