"""The host and runner registries' known-id plural reads (component tier): each value equals the singular answer,
across a lowered batch boundary, for retired, hosted and never-connected records, and across tenants."""

from __future__ import annotations

from pathlib import Path

import pytest

from blizzard.foundation.store import batching as batching_module
from tests.support import build_hub, seed_runner

pytestmark = pytest.mark.component


def _add_host(client, name: str) -> str:
    resp = client.post("/api/hosts", json={"name": name})
    assert resp.status_code == 201, resp.text
    return str(resp.json()["host_id"])


def test_get_hosts_equals_get_host_across_a_batch_boundary(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    hub = build_hub(tmp_path)
    ids = [_add_host(hub.client, f"box{i}") for i in range(7)]
    assert hub.client.post(f"/api/hosts/{ids[2]}/retire", json={}).status_code == 200
    hosts = hub.services.hosts

    result = hosts.get_hosts([*ids, ids[0], "ho_missing"])

    assert set(result) == set(ids)
    assert result[ids[2]].retired
    for host_id in ids:
        assert result[host_id] == hosts.get_host(host_id)
    assert hosts.get_hosts([]) == {}


def test_get_runners_equals_get_runner_for_hosted_standalone_and_retired(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(batching_module, "BATCH_SIZE", 3)
    hub = build_hub(tmp_path)
    ids = [f"rn_{i}" for i in range(7)]
    for runner_id in ids[:-1]:
        seed_runner(hub, runner_id)
    seed_runner(hub, ids[-1], register=False)
    host_id = _add_host(hub.client, "box")
    assert hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ids[:2]}).status_code == 200
    assert hub.client.post(f"/api/runners/{ids[3]}/retire", json={"by": "op"}).status_code == 200
    registry = hub.services.registry

    result = registry.get_runners([*reversed(ids), ids[0], "rn_missing"])

    assert set(result) == set(ids)
    assert result[ids[3]].retired
    for runner_id in ids:
        assert result[runner_id] == registry.get_runner(runner_id)
    assert registry.get_runners([]) == {}


def test_plural_reads_are_tenant_scoped(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    mine = hub.add_tenant("mine")
    other = hub.add_tenant("other")
    seed_runner(other.services, other.runner_id("r1"), register=False)
    other_host = _add_host(other.client, "theirs")

    assert mine.services.registry.get_runners([other.runner_id("r1")]) == {}
    assert mine.services.hosts.get_hosts([other_host]) == {}


def test_adopt_refuses_the_first_missing_id_before_a_known_invalid_one(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    host_id = _add_host(hub.client, "box")
    seed_runner(hub, "rn_a")
    seed_runner(hub, "rn_dead")
    assert hub.client.post("/api/runners/rn_dead/retire", json={"by": "op"}).status_code == 200
    url = f"/api/hosts/{host_id}/adoptions"

    refused = hub.client.post(url, json={"runner_ids": ["rn_dead", "rn_gone1", "rn_a", "rn_gone2"]})

    assert refused.status_code == 404
    assert "rn_gone1" in refused.text
    assert hub.client.get(f"/api/hosts/{host_id}").json()["runner_ids"] == []
