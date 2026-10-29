"""The runner's bounded, chunk-filtered invocation provenance read."""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

import pytest

from blizzard.runner.config import RunnerConfig
from blizzard.runner.harness.usage import UsageKind, UsageSample
from blizzard.runner.usage.repository import InvocationCost
from tests.config_inputs import create_runner_app
from tests.runner_client import runner_client
from tests.runner_fakes import make_host_stores, make_store, make_stores


@pytest.mark.component
def test_invocation_usage_is_chunk_filtered_paged_and_attributed(tmp_path: Path) -> None:
    store = make_store(f"sqlite:///{tmp_path / 'runner.db'}")
    app = create_runner_app(
        RunnerConfig(root=tmp_path, db_url=f"sqlite:///{tmp_path / 'runner.db'}"),
        runner_stores=make_stores(store),
        host_stores=make_host_stores(store),
    )
    samples: list[tuple[str, str, UsageKind, str]] = [
        ("ch_1", "lease_1", "spawn", "claude_code"),
        ("ch_other", "lease_2", "spawn", "opencode"),
        ("ch_1", "lease_1", "judge", "claude_code"),
    ]
    for chunk, lease, kind, harness in samples:
        store.record_usage(
            lease_id=lease,
            chunk_id=chunk,
            node_id="nd_build",
            epoch=1,
            generation=1 if kind == "spawn" else 2,
            sample=UsageSample(
                kind=kind,
                model="model",
                harness_id=harness,
                input_tokens=1,
                output_tokens=1,
                cache_read_tokens=0,
                cache_create_tokens=0,
                cost_usd=None,
            ),
            cost=InvocationCost(cost_usd=None, estimated_cost_usd=None),
            recorded_at=datetime(2026, 10, 1, tzinfo=UTC),
        )

    client = runner_client(app)
    first = client.get("/api/chunks/ch_1/usage", params={"limit": 1})
    assert first.status_code == 200
    assert [(item["lease_id"], item["kind"], item["harness_id"]) for item in first.json()["items"]] == [
        ("lease_1", "spawn", "claude_code")
    ]
    second = client.get("/api/chunks/ch_1/usage", params={"limit": 1, "offset": 1})
    assert [(item["lease_id"], item["kind"], item["harness_id"]) for item in second.json()["items"]] == [
        ("lease_1", "judge", "claude_code")
    ]
    assert client.get("/api/chunks/ch_unknown/usage").json()["items"] == []
    assert client.get("/api/chunks/ch_1/usage", params={"limit": 201}).status_code == 422
