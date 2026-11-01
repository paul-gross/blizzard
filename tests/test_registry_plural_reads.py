"""The host and runner registries' known-id plural reads (component tier): each value equals the singular answer,
across a lowered batch boundary, for retired, hosted and never-connected records, and across tenants."""

from __future__ import annotations

import dataclasses
from datetime import timedelta
from pathlib import Path

import pytest

from blizzard.foundation.store import batching as batching_module
from blizzard.hub.api.runners import hosts_of
from blizzard.hub.store.internal.runner_registry_store import RunnerRegistryStore
from tests.support import HubHarness, build_hub, count_queries, seed_runner

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


def test_get_hosts_statements_are_flat_in_the_ids_selected_and_blind_to_unrelated_hosts(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ids = [_add_host(hub.client, f"box{i}") for i in range(12)]
    hosts = hub.services.hosts

    few = count_queries(hub.engine, lambda: hosts.get_hosts(ids[:2]))
    many = count_queries(hub.engine, lambda: hosts.get_hosts(ids[:10]))

    assert few == many
    assert set(hosts.get_hosts(ids[:2])) == set(ids[:2])
    # Lifecycle history on unrelated hosts adds no statement and no row to the selection's answer.
    for host_id in ids[2:]:
        assert hub.client.post(f"/api/hosts/{host_id}/retire", json={}).status_code == 200
    assert count_queries(hub.engine, lambda: hosts.get_hosts(ids[:2])) == few
    assert set(hosts.get_hosts(ids[:2])) == set(ids[:2])


def test_get_runners_statements_are_flat_in_the_ids_selected_and_blind_to_unrelated_runners(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    ids = [f"rn_{i}" for i in range(12)]
    for runner_id in ids:
        seed_runner(hub, runner_id)
    registry = hub.services.registry

    few = count_queries(hub.engine, lambda: registry.get_runners(ids[:2]))
    many = count_queries(hub.engine, lambda: registry.get_runners(ids[:10]))

    assert few == many
    for runner_id in ids[2:]:
        assert hub.client.post(f"/api/runners/{runner_id}/retire", json={"by": "op"}).status_code == 200
        assert hub.client.post(f"/api/runners/{runner_id}/reinstate", json={"by": "op"}).status_code == 200
    assert count_queries(hub.engine, lambda: registry.get_runners(ids[:2])) == few
    assert set(registry.get_runners(ids[:2])) == set(ids[:2])


def test_get_runners_matches_get_runner_for_reinstated_never_connected_and_standalone_usage(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    for runner_id in ("rn_back", "rn_alone", "rn_hosted"):
        seed_runner(hub, runner_id)
    seed_runner(hub, "rn_new", register=False)
    registry = hub.services.registry
    assert isinstance(registry, RunnerRegistryStore)
    now = hub.clock.now()
    assert hub.client.post("/api/runners/rn_back/retire", json={"by": "op"}).status_code == 200
    assert hub.client.post("/api/runners/rn_back/reinstate", json={"by": "op"}).status_code == 200
    registry.record_external_usage(
        "rn_alone", slug="claude-max", name="Claude Max", sampled_at=now, windows_json="[]", at=now
    )
    registry.record_external_usage_miss(
        "rn_alone", slug="codex", name="Codex", missed_at=now - timedelta(minutes=1), reason="down", at=now
    )
    host_id = _add_host(hub.client, "box")
    assert hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": ["rn_hosted"]}).status_code == 200
    ids = ["rn_back", "rn_alone", "rn_hosted", "rn_new"]

    result = registry.get_runners(ids)

    for runner_id in ids:
        assert result[runner_id] == registry.get_runner(runner_id)
    assert not result["rn_back"].retired
    bare = dataclasses.replace(result["rn_alone"], subscription_usage=(), subscription_usage_misses=())
    assert result["rn_alone"] != bare


def test_hosts_of_reads_the_named_hosts_only_and_a_missing_host_falls_back(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    hub = build_hub(tmp_path)
    first, second = _add_host(hub.client, "one"), _add_host(hub.client, "two")
    for runner_id, host_id in (("rn_1", first), ("rn_2", second)):
        seed_runner(hub, runner_id)
        assert hub.client.post(f"/api/hosts/{host_id}/adoptions", json={"runner_ids": [runner_id]}).status_code == 200
    services = hub.services
    monkeypatch.setattr(services.hosts, "list_hosts", lambda **_: pytest.fail("a full host list was read"))
    registrations = [services.registry.get_runner(r) for r in ("rn_1", "rn_2")]
    assert all(r is not None for r in registrations)
    ghost = dataclasses.replace(registrations[0], host_id="ho_gone")  # type: ignore[type-var]

    found = hosts_of(services, registrations)  # type: ignore[arg-type]
    with_missing = hosts_of(services, [*registrations, ghost])  # type: ignore[list-item]

    assert set(found) == {first, second}
    assert with_missing == found
    listed = hub.client.get("/api/runners").json()["runners"]
    assert {r["runner_id"]: r["host_id"] for r in listed} == {"rn_1": first, "rn_2": second}


def _runner_ids(hub: HubHarness, host_id: str) -> list[str]:
    return hub.client.get(f"/api/hosts/{host_id}").json()["runner_ids"]


def test_adopt_and_release_refuse_the_first_failure_in_operator_order_and_write_nothing(tmp_path: Path) -> None:
    hub = build_hub(tmp_path)
    mine, theirs = _add_host(hub.client, "mine"), _add_host(hub.client, "theirs")
    for runner_id in ("rn_a", "rn_b", "rn_c", "rn_held", "rn_dead"):
        seed_runner(hub, runner_id)
    assert hub.client.post(f"/api/hosts/{theirs}/adoptions", json={"runner_ids": ["rn_held"]}).status_code == 200
    assert hub.client.post("/api/runners/rn_dead/retire", json={"by": "op"}).status_code == 200
    adopt = f"/api/hosts/{mine}/adoptions"
    release = f"/api/hosts/{mine}/releases"

    # Two missing ids: the first named is the one refused, whatever order SQL would return.
    two_missing = hub.client.post(adopt, json={"runner_ids": ["rn_c", "rn_zz", "rn_aa"]})
    assert two_missing.status_code == 404 and "rn_zz" in two_missing.text
    # Already hosted, then retired: the first invalid known id in operator order.
    assert hub.client.post(adopt, json={"runner_ids": ["rn_b", "rn_held", "rn_dead"]}).status_code == 409
    assert _runner_ids(hub, mine) == []
    # Duplicates adopt once, in one write; the add path refuses identically and adds no host.
    assert hub.client.post(adopt, json={"runner_ids": ["rn_c", "rn_a", "rn_c"]}).status_code == 200
    assert sorted(_runner_ids(hub, mine)) == ["rn_a", "rn_c"]
    add = hub.client.post("/api/hosts", json={"name": "third", "adopt": ["rn_b", "rn_gone"]})
    assert add.status_code == 404
    assert sorted(h["host_name"] for h in hub.client.get("/api/hosts").json()["hosts"]) == ["mine", "theirs"]
    # Release: a runner under another host fails the whole release, as does a missing one.
    assert hub.client.post(release, json={"runner_ids": ["rn_a", "rn_held"]}).status_code == 404
    assert hub.client.post(release, json={"runner_ids": ["rn_a", "rn_gone"]}).status_code == 404
    assert sorted(_runner_ids(hub, mine)) == ["rn_a", "rn_c"]
    assert hub.client.post(release, json={"runner_ids": ["rn_c", "rn_a", "rn_c"]}).status_code == 200
    assert _runner_ids(hub, mine) == []
